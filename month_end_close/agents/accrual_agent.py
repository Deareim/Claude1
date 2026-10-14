"""Accrual Agent: missing accruals and prepaid amortization."""
from __future__ import annotations

from agents.base import BaseAgent
from tools import accruals


class AccrualAgent(BaseAgent):
    name = "Accrual Agent"

    def run(self) -> list:
        ctx = self.ctx
        self.think("Scanning recurring commitments, payroll cut-off and the prepaid register for entries not yet booked.")
        findings = accruals.run_all(ctx.conn, ctx.period)
        ctx.metrics["accrual_findings"] = findings
        self.act(f"Found {len(findings)} candidate entries "
                 f"(${sum(f.amount for f in findings):,.2f} total).")
        for f in findings:
            self.think(f"[{f.kind}] {f.subject}: {f.evidence}. Draft ${f.amount:,.2f}.")
            if f.duplicate_of:
                self.resolve("duplicate_skipped", f"{f.subject} accrual ${f.amount:,.2f} already covered by {f.duplicate_of} "
                                                  f"- agent draft suppressed to avoid double-booking", f.amount)
                continue
            self.submit_je(f.je, f.kind)
        return findings
