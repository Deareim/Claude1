"""Human-in-the-loop safety gate: escalation queue + approval policy."""
from __future__ import annotations

import time
from dataclasses import dataclass

import config
from tools.models import JournalEntry


@dataclass
class ApprovalRequest:
    req_id: str
    requested_by: str
    category: str
    title: str
    amount: float
    reason: str
    step: str | None = None
    je: JournalEntry | None = None
    detail: str = ""
    status: str = "pending"      # pending | approved | rejected
    decided_by: str = ""
    note: str = ""


def demo_accountant(req: ApprovalRequest) -> tuple[str, str]:
    """Scripted reviewer used in non-interactive demo runs."""
    if req.category == "unbalanced_je":
        return "rejected", "Out of balance - returned to preparer to correct before resubmitting."
    notes = {
        "out_of_balance": "Confirmed Stark invoice mis-keyed in GL; correcting entry approved.",
        "unidentified_deposit": "Parked in suspense pending remittance advice from treasury.",
        "returned_item": "NSF confirmed with bank; AR team to reinstate invoice.",
        "flux_unexplained": "Commentary reviewed and accepted.",
        "close_signoff": "Reviewed close package; signed off.",
    }
    return "approved", notes.get(req.category, "Reviewed supporting schedule; approved.")


class HITLGate:
    """mode: 'scripted' (demo reviewer decides), 'interactive' (terminal prompt), 'defer' (leave pending)."""

    def __init__(self, bus, mode: str = "scripted", decider=demo_accountant, reviewer: str = "controller"):
        self.bus, self.mode, self.decider, self.reviewer = bus, mode, decider, reviewer
        self.requests: list[ApprovalRequest] = []

    @staticmethod
    def je_review_reasons(je: JournalEntry) -> list[str]:
        reasons = []
        if not je.is_balanced:
            reasons.append(f"entry out of balance (Dr ${je.total_debits:,.2f} vs Cr ${je.total_credits:,.2f})")
        if je.amount >= config.HITL_JE_THRESHOLD:
            reasons.append(f"high-dollar JE ${je.amount:,.2f} >= ${config.HITL_JE_THRESHOLD:,.0f} threshold")
        return reasons

    def submit(self, requested_by: str, category: str, title: str, amount: float, reason: str,
               je: JournalEntry | None = None, detail: str = "") -> ApprovalRequest:
        req = ApprovalRequest(f"HITL-{len(self.requests) + 1:03d}", requested_by, category, title, amount, reason,
                              self.bus.current_step, je, detail)
        self.requests.append(req)
        self.bus.emit("HITL Gate", "escalated", f"{req.req_id} {title} - {reason}", amount=amount,
                      req_id=req.req_id, category=category)
        if self.mode == "scripted":
            self.decide(req, *self.decider(req), by=f"{self.reviewer} (scripted)")
        elif self.mode == "interactive":
            self._prompt(req)
        return req

    def decide(self, req: ApprovalRequest, decision: str, note: str, by: str):
        req.status, req.note, req.decided_by = decision, note, by
        self.bus.emit("HITL Gate", "approval", f"{req.req_id} {decision.upper()} by {by}: {note}",
                      req_id=req.req_id, decision=decision)

    def _prompt(self, req: ApprovalRequest):
        from rich.console import Console
        from rich.panel import Panel
        from rich.prompt import Prompt
        con = Console()
        body = f"[bold]{req.title}[/]\nAmount: ${req.amount:,.2f}\nWhy escalated: {req.reason}"
        if req.je:
            body += "\n" + "\n".join(f"  {l.account}  Dr {l.debit:>12,.2f}  Cr {l.credit:>12,.2f}" for l in req.je.lines)
            if req.je.rationale:
                body += f"\nRationale: {req.je.rationale}"
        con.print(Panel(body, title=f"APPROVAL NEEDED {req.req_id}", border_style="yellow"))
        choice = Prompt.ask("[a]pprove / [r]eject / [d]efer", choices=["a", "r", "d"], default="a")
        if choice == "d":
            return
        note = Prompt.ask("Note", default="")
        self.decide(req, "approved" if choice == "a" else "rejected", note, by=f"{self.reviewer} (interactive)")

    def pending(self, step: str | None = None) -> list[ApprovalRequest]:
        return [r for r in self.requests if r.status == "pending" and (step is None or r.step == step)]
