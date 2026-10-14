"""Reconciliation Agent: bank matching, unmatched-item triage and sub-ledger tie-out."""
from __future__ import annotations

import itertools

import config
from agents.base import BaseAgent
from tools import reconciliation as rec
from tools.models import JournalEntry


class ReconciliationAgent(BaseAgent):
    name = "Reconciliation Agent"

    def run_bank_rec(self) -> dict:
        ctx, period = self.ctx, self.ctx.period
        bank, book = rec.load_bank(ctx.conn, period), rec.build_book_cash(ctx.conn, period)
        self.think(f"Loaded {len(bank)} bank lines and {len(book)} book cash movements for {period}. "
                   f"Rules: amount tol ${config.BANK_AMOUNT_TOL:.2f}, date window {config.BANK_DATE_WINDOW}d.")
        res = rec.match_transactions(bank, book)
        kinds = {k: sum(1 for m in res.matches if m.kind == k) for k in ("exact", "tolerance", "batch")}
        self.act(f"Matched {len(res.matches)} groups: {kinds['exact']} exact, {kinds['tolerance']} within tolerance, "
                 f"{kinds['batch']} batch/lockbox.")
        for m in res.matches:
            if m.kind == "batch":
                self.think(f"{m.bank_ids[0]} (${m.bank_amount:,.2f}) = sum of {len(m.book_ids)} receipts "
                           f"({', '.join(m.book_ids)}) -> batch deposit match.")
            elif m.kind == "tolerance":
                self.think(f"{m.bank_ids[0]} differs from {m.book_ids[0]} by ${m.difference:+.2f}: inside tolerance, matched.")
        ctx.resolved.append(dict(agent=self.name, category="matched", amount=0.0, je_id=None, step=ctx.bus.current_step,
                                 description=f"{len(res.matches)} bank/book groups matched automatically"))
        counter = itertools.count(1)
        nid = lambda: f"RCN-{next(counter):03d}"  # noqa: E731

        # --- unmatched bank items, grouped by category -----------------
        groups: dict[str, list] = {}
        for r in res.unmatched_bank.itertuples():
            groups.setdefault(rec.classify_unmatched_bank(r.description, r.amount), []).append(r)
        self.think(f"{len(res.unmatched_bank)} bank lines unmatched -> categories: "
                   + ", ".join(f"{k} x{len(v)}" for k, v in groups.items()))

        fees = groups.get("bank_fee", [])
        tol_total = round(-sum(m.difference for m in res.matches), 2)  # remitter short-pays: expense to bank charges
        fee_total = round(-sum(r.amount for r in fees), 2) + (tol_total if tol_total > 0 else 0)
        if fee_total:
            je = JournalEntry.simple(nid(), config.PERIOD_END, "Record bank fees and remittance short-pays",
                                     "6500", "1000", fee_total, source="recon",
                                     rationale=f"{len(fees)} unmatched fee lines (${-sum(r.amount for r in fees):,.2f}) "
                                               f"+ ${tol_total:,.2f} tolerance short-pay on matched wire")
            self.think(f"Bank fees total ${fee_total:,.2f} - below auto-resolve limit ${config.AUTO_FEE_MAX:,.0f}"
                       if fee_total <= config.AUTO_FEE_MAX else f"Bank fees ${fee_total:,.2f} exceed auto limit.")
            self.submit_je(je, "bank_fee", None if fee_total <= config.AUTO_FEE_MAX else "fees above auto limit")
        for r in groups.get("interest", []):
            je = JournalEntry.simple(nid(), config.PERIOD_END, "Record bank interest earned", "1000", "7000", r.amount,
                                     source="recon", rationale=f"{r.bank_id} {r.description}")
            self.submit_je(je, "interest")
        for r in groups.get("returned_item", []):
            je = JournalEntry.simple(nid(), r.txn_date, f"Reverse NSF receipt - {r.description}", "1100", "1000",
                                     abs(r.amount), source="recon",
                                     rationale=f"{r.bank_id}: customer cheque returned; reinstate receivable")
            self.think(f"{r.bank_id} ${r.amount:,.2f} is a returned item with no book entry - customer credit exposure, "
                       f"needs accountant sign-off.")
            self.submit_je(je, "returned_item", f"returned item ${abs(r.amount):,.2f} >= ${config.HITL_UNMATCHED_THRESHOLD:,.0f}")
        for r in groups.get("unidentified", []):
            je = JournalEntry.simple(nid(), r.txn_date, f"Unapplied cash - {r.description}", "1000", "2900", r.amount,
                                     source="recon", rationale=f"{r.bank_id}: no matching invoice/remittance; park in suspense")
            self.think(f"{r.bank_id} ${r.amount:,.2f} matches no open AR (amount, customer or reference) - cannot auto-apply.")
            self.submit_je(je, "unidentified_deposit", f"unidentified receipt ${r.amount:,.2f} >= ${config.HITL_UNMATCHED_THRESHOLD:,.0f}")

        # --- unmatched book items ----------------------------------------
        for r in res.unmatched_book.itertuples():
            if rec.classify_unmatched_book(r.txn_date) == "timing":
                self.resolve("timing", f"{r.book_id} {r.description} ${r.amount:,.2f} - outstanding at month-end "
                                       f"(recorded {r.txn_date}); will clear next period", r.amount)
            else:
                self.ctx.gate.submit(self.name, "stale_book_item", f"{r.book_id} {r.description}", abs(r.amount),
                                     "book item >5 days before month-end with no bank clearing")

        s = rec.bank_rec_summary(ctx.conn, period, res, bank, book)
        ctx.metrics["bank_rec"] = s
        ok = abs(s["difference"]) < 0.005
        self.act(f"Proof: adjusted bank ${s['adjusted_bank']:,.2f} vs adjusted book ${s['adjusted_book']:,.2f} "
                 f"-> difference ${s['difference']:,.2f} ({'RECONCILED' if ok else 'OUT OF BALANCE'})")
        if not ok:
            ctx.gate.submit(self.name, "out_of_balance", "Bank reconciliation does not prove", abs(s["difference"]),
                            f"adjusted balances differ by ${s['difference']:,.2f}")
        return s

    def run_tieout(self) -> list:
        ctx = self.ctx
        ties = rec.subledger_tieout(ctx.conn, ctx.period)
        ctx.metrics["tieout"] = ties
        self.think(f"Comparing {len(ties)} sub-ledger totals to GL control accounts (tolerance ${config.SUBLEDGER_TOL:.2f}).")
        handled = set()
        for t in ties:
            if t.ok:
                self.act(f"{t.account} {t.name}: sub-ledger ${t.subledger:,.2f} = GL ${t.gl:,.2f} OK")
        bad = [t for t in ties if not t.ok]
        for t in bad:
            if t.account in handled:
                continue
            self.think(f"{t.account} {t.name}: sub-ledger ${t.subledger:,.2f} vs GL ${t.gl:,.2f} (diff ${t.diff:+,.2f}).")
            twin = next((x for x in bad if x.account == "1100" and t.account == "4100" and abs(x.diff - t.diff) < 0.01), None)
            if t.account in ("4000", "4100"):
                debit, credit = ("1100", t.account) if t.diff > 0 else (t.account, "1100")
                je = JournalEntry.simple("TIE-001", config.PERIOD_END, f"Correct GL {t.account} to AR sub-ledger",
                                         debit, credit, abs(t.diff), source="tieout",
                                         rationale="GL revenue lower than AR sub-ledger invoicing"
                                                   + ("; same difference sits in AR control, so one entry fixes both" if twin else ""))
                self.submit_je(je, "out_of_balance", f"GL out of balance with sub-ledger by ${abs(t.diff):,.2f}")
                if twin:
                    handled.add("1100")
            elif t.account not in handled:
                ctx.gate.submit(self.name, "out_of_balance", f"{t.account} {t.name} out of balance", abs(t.diff),
                                f"difference ${t.diff:+,.2f} with no automatic correction")
        return ties
