"""Variance & Reporting Agent: M-1 vs M0 flux and drafted explanations."""
from __future__ import annotations

import config
from agents.base import BaseAgent
from tools import flux


class VarianceReportingAgent(BaseAgent):
    name = "Variance & Reporting Agent"

    def run(self) -> list[dict]:
        ctx = self.ctx
        df = flux.run_flux(ctx.conn, ctx.prior, ctx.period)
        flagged = df[df["flagged"]]
        ctx.metrics["flux_total"], ctx.metrics["flux_flagged"] = len(df), len(flagged)
        self.think(f"Compared {ctx.prior} vs {ctx.period} on {len(df)} P&L accounts using >= {config.FLUX_PCT:.0%} "
                   f"or >= ${config.FLUX_ABS:,.0f} thresholds -> {len(flagged)} flagged.")
        for r in flagged.itertuples():
            drivers = flux.driver_analysis(ctx.conn, r.account, ctx.prior, ctx.period)
            same = [d for d in drivers if d["delta"] * r.variance > 0]
            explained = round(sum(d["delta"] for d in same), 2)
            residual = round(r.variance - explained, 2)
            top = "; ".join(f"{d['driver']} {d['delta']:+,.0f}" for d in drivers[:3]) or "no sub-ledger drivers found"
            direction = "increased" if r.variance > 0 else "decreased"
            text = (f"{r.name} ({r.account}) {direction} ${abs(r.variance):,.0f} ({r.pct:+.1%}) vs {ctx.prior}. "
                    f"Drivers: {top}.")
            status = "explained"
            if abs(residual) >= config.FLUX_UNEXPLAINED_ESCALATE:
                text += f" Unexplained residual ${residual:+,.0f}."
                status = "needs_review"
            elif abs(residual) >= 1:
                text += f" Residual ${residual:+,.0f} immaterial."
            entry = dict(account=r.account, name=r.name, prior=r.prior, current=r.current, variance=r.variance,
                         pct=r.pct, reason=r.reason, explanation=text, status=status, residual=residual)
            ctx.commentary.append(entry)
            self.think(f"{r.account} {r.name}: {r.variance:+,.0f} ({r.pct:+.1%}) - explained ${explained:,.0f}, "
                       f"residual ${residual:+,.0f}.")
            if status == "needs_review":
                ctx.gate.submit(self.name, "flux_unexplained", f"{r.account} {r.name} variance", abs(residual),
                                f"${abs(residual):,.0f} of ${abs(r.variance):,.0f} variance unexplained", detail=text)
            else:
                self.resolve("flux_explained", text, r.variance)
        return ctx.commentary
