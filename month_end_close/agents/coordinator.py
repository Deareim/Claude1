"""Close Coordinator: runs the checklist, delegates to specialists, tracks completion."""
from __future__ import annotations

from dataclasses import dataclass

import config
from agents.accrual_agent import AccrualAgent
from agents.base import BaseAgent, CloseContext, EventBus
from agents.hitl import HITLGate
from agents.reconciliation_agent import ReconciliationAgent
from agents.variance_agent import VarianceReportingAgent
from tools import ledger


@dataclass
class Step:
    step_id: str
    name: str
    owner: str
    status: str = "pending"     # pending | running | done | blocked
    detail: str = ""


class CloseCoordinator(BaseAgent):
    name = "Close Coordinator"

    def __init__(self, conn, bus: EventBus | None = None, gate_mode: str = "scripted", period: str = config.PERIOD,
                 prior: str = config.PRIOR_PERIOD):
        bus = bus or EventBus()
        gate = HITLGate(bus, mode=gate_mode)
        super().__init__(CloseContext(conn=conn, bus=bus, gate=gate, period=period, prior=prior))
        self.recon, self.accrual = ReconciliationAgent(self.ctx), AccrualAgent(self.ctx)
        self.variance = VarianceReportingAgent(self.ctx)
        self.steps = [
            Step("S1", "Validate opening trial balance", self.name),
            Step("S2", "Bank reconciliation", self.recon.name),
            Step("S3", "Sub-ledger to GL tie-out", self.recon.name),
            Step("S4", "Accruals & amortization", self.accrual.name),
            Step("S5", "Review manual journal entries", self.name),
            Step("S6", "Post approved entries & re-verify TB", self.name),
            Step("S7", "Flux analysis & commentary", self.variance.name),
            Step("S8", "Close package & sign-off", self.name),
        ]
        self.handlers = {"S1": self._s1, "S2": self.recon.run_bank_rec, "S3": self.recon.run_tieout,
                         "S4": self.accrual.run, "S5": self._s5, "S6": self._s6, "S7": self.variance.run,
                         "S8": self._s8}

    # --- progress ---------------------------------------------------------
    @property
    def completion_pct(self) -> float:
        score = sum(1.0 if s.status == "done" else 0.5 if s.status == "blocked" else 0.0 for s in self.steps)
        return round(100 * score / len(self.steps), 1)

    def run(self) -> dict:
        bus = self.ctx.bus
        bus.emit(self.name, "info", f"Starting month-end close for {self.ctx.period} ({len(self.steps)} checklist steps)",
                 pct=self.completion_pct)
        for s in self.steps:
            bus.current_step = s.step_id
            s.status = "running"
            if s.owner != self.name:
                bus.emit(self.name, "action", f"Delegating {s.step_id} '{s.name}' to {s.owner}")
            bus.emit(self.name, "step_start", s.name, step_name=s.name, owner=s.owner)
            try:
                self.handlers[s.step_id]()
            except Exception as exc:  # surface, don't crash the whole close
                bus.emit(self.name, "error", f"{s.step_id} failed: {exc}")
                s.status, s.detail = "blocked", str(exc)
            else:
                pend = self.ctx.gate.pending(s.step_id)
                s.status = "blocked" if pend else "done"
                s.detail = f"{len(pend)} approval(s) pending" if pend else "complete"
            bus.emit(self.name, "step_end", f"{s.name}: {s.status}", status=s.status, pct=self.completion_pct,
                     step_name=s.name)
        bus.current_step = None
        return self.report()

    # --- coordinator-owned steps -----------------------------------------
    def _s1(self):
        d, c = ledger.tb_totals(self.ctx.conn, self.ctx.period)
        self.think(f"Opening TB totals: Dr ${d:,.2f} / Cr ${c:,.2f}")
        if abs(d - c) > 0.005:
            self.ctx.gate.submit(self.name, "out_of_balance", "Opening trial balance out of balance", abs(d - c),
                                 f"Dr ${d:,.2f} != Cr ${c:,.2f}")
        else:
            self.act("Trial balance is in balance; sub-ledgers and bank feeds loaded.")
        n_un = len(ledger.load_unposted_jes(self.ctx.conn))
        self.think(f"{n_un} manual JEs waiting to be posted; prior period {self.ctx.prior} available for flux.")

    def _s5(self):
        jes = ledger.load_unposted_jes(self.ctx.conn)
        self.think(f"Validating {len(jes)} preparer-submitted JEs: balance check, account check, dollar threshold.")
        known = {r[0] for r in self.ctx.conn.execute("SELECT account FROM chart_of_accounts")}
        for je in jes:
            bad = [l.account for l in je.lines if l.account not in known]
            if bad:
                self.ctx.gate.submit(self.name, "invalid_account", f"{je.je_id}: {je.description}", je.amount,
                                     f"unknown account(s) {bad}", je=je)
                continue
            self.submit_je(je)

    def _s6(self):
        ctx = self.ctx
        self.think(f"Posting {len(ctx.approved)} approved JEs ({len(ctx.rejected)} rejected stay out of the GL).")
        total = 0.0
        for je, approver in ctx.approved:
            ledger.post_journal_entry(ctx.conn, je, ctx.period, approver)
            total += je.amount
        self.act(f"Posted {len(ctx.approved)} entries, ${total:,.2f} total debits.")
        ok = ledger.tb_is_balanced(ctx.conn, ctx.period)
        d, c = ledger.tb_totals(ctx.conn, ctx.period)
        ctx.metrics["post_tb"] = (d, c)
        self.act(f"Post-adjustment TB: Dr ${d:,.2f} / Cr ${c:,.2f} -> {'BALANCED' if ok else 'OUT OF BALANCE'}")
        if not ok:
            ctx.gate.submit(self.name, "out_of_balance", "Post-adjustment TB out of balance", abs(d - c), "TB mismatch")

    def _s8(self):
        ctx = self.ctx
        open_prior = [s for s in self.steps[:-1] if s.status != "done"]
        if open_prior:
            self.think("Cannot request sign-off: "
                       + ", ".join(f"{s.step_id} ({s.status})" for s in open_prior) + " still open.")
            ctx.gate.submit(self.name, "close_blocked", "Close package incomplete", 0.0,
                            f"{len(open_prior)} prior step(s) not complete")
            return
        self.think("All upstream steps complete; requesting controller sign-off on close package.")
        ctx.gate.submit(self.name, "close_signoff", f"Sign-off {ctx.period} close package", 0.0,
                        "final review required by policy")
        self.act(f"Close package assembled: {len(ctx.approved)} JEs posted, {len(ctx.resolved)} items auto-resolved, "
                 f"{len(ctx.commentary)} variance commentaries.")

    # --- reporting --------------------------------------------------------
    def report(self) -> dict:
        ctx = self.ctx
        return dict(
            period=ctx.period, completion_pct=self.completion_pct,
            steps=[vars(s) for s in self.steps],
            auto_resolved=len([r for r in ctx.resolved if r["category"] != "matched"]),
            posted_jes=[dict(je_id=j.je_id, description=j.description, amount=j.amount, source=j.source, approved_by=a)
                        for j, a in ctx.approved],
            rejected_jes=[j.je_id for j in ctx.rejected],
            approvals=[dict(req_id=r.req_id, category=r.category, title=r.title, amount=r.amount, status=r.status,
                            decided_by=r.decided_by, note=r.note) for r in ctx.gate.requests],
            pending_approvals=len(ctx.gate.pending()),
            commentary=ctx.commentary, metrics={k: v for k, v in ctx.metrics.items() if k == "bank_rec"},
        )
