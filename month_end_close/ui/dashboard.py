"""Rich CLI dashboard: streams agent reasoning, resolved items and approvals step by step."""
from __future__ import annotations

import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

STYLE = {"reasoning": ("dim cyan", "think"), "action": ("white", "act  "), "resolved": ("green", "AUTO "),
         "escalated": ("bold yellow", "ESCAL"), "approval": ("bold magenta", "HUMAN"), "info": ("bold blue", "info "),
         "error": ("bold red", "ERROR")}
STATUS = {"pending": "[dim]o pending[/]", "running": "[cyan]> running[/]", "done": "[green]v done[/]",
          "blocked": "[yellow]! blocked[/]"}


class Dashboard:
    def __init__(self, coordinator, console: Console | None = None, delay: float = 0.0):
        self.co, self.con, self.delay = coordinator, console or Console(), delay
        coordinator.ctx.bus.subscribe(self.on_event)

    # --- live stream --------------------------------------------------------
    def on_event(self, ev):
        if ev.kind == "step_start":
            idx = next(i for i, s in enumerate(self.co.steps, 1) if s.step_id == ev.step)
            self.con.print()
            self.con.rule(f"[bold]Step {idx}/{len(self.co.steps)}: {ev.message}[/]  [dim]owner: {ev.data.get('owner')}[/]")
            return
        if ev.kind == "step_end":
            self.con.print(self.checklist())
            self._sleep()
            return
        style, tag = STYLE.get(ev.kind, ("white", "     "))
        t = Text()
        t.append(f"{tag} ", style=style)
        t.append(f"{ev.agent:<27}", style="bold")
        t.append(ev.message, style=style)
        self.con.print(t, overflow="fold")
        self._sleep()

    def _sleep(self):
        if self.delay:
            time.sleep(self.delay)

    # --- tables -------------------------------------------------------------
    def progress_bar(self, width: int = 30) -> str:
        pct = self.co.completion_pct
        fill = int(width * pct / 100)
        return f"[green]{'█' * fill}[/][dim]{'░' * (width - fill)}[/] [bold]{pct:.1f}%[/]"

    def checklist(self) -> Panel:
        t = Table(show_header=False, box=None, padding=(0, 1))
        for s in self.co.steps:
            t.add_row(s.step_id, s.name, s.owner, STATUS[s.status], f"[dim]{s.detail}[/]")
        pend = len(self.co.ctx.gate.pending())
        return Panel(t, title=f"Close checklist  {self.progress_bar()}  |  pending approvals: {pend}",
                     border_style="blue")

    def header(self):
        self.con.print(Panel.fit("[bold]Month-End Close Agent Demo[/]\n"
                                 f"Period {self.co.ctx.period} (vs {self.co.ctx.prior})  |  multi-agent, human-in-the-loop",
                                 border_style="cyan"))

    def summary(self, report: dict):
        con, ctx = self.con, self.co.ctx
        con.print()
        con.rule("[bold]CLOSE SUMMARY")

        t = Table(title="Human approvals (HITL gate)", header_style="bold")
        for c in ("ID", "Category", "Item", "Amount", "Status", "Decided by / note"):
            t.add_column(c, overflow="fold")
        colour = {"approved": "green", "rejected": "red", "pending": "yellow"}
        for r in ctx.gate.requests:
            t.add_row(r.req_id, r.category, r.title, f"${r.amount:,.2f}", f"[{colour[r.status]}]{r.status}[/]",
                      f"{r.decided_by} - {r.note}" if r.decided_by else "awaiting reviewer")
        con.print(t)

        t = Table(title="Auto-resolved by agents", header_style="bold")
        for c in ("Agent", "Type", "Resolution", "Amount"):
            t.add_column(c, overflow="fold")
        for r in ctx.resolved:
            t.add_row(r["agent"], r["category"], r["description"], f"${r['amount']:,.2f}" if r["amount"] else "")
        con.print(t)

        t = Table(title="Journal entries posted", header_style="bold")
        for c in ("JE", "Source", "Description", "Amount", "Approved by"):
            t.add_column(c, overflow="fold")
        for j in report["posted_jes"]:
            t.add_row(j["je_id"], j["source"], j["description"], f"${j['amount']:,.2f}", j["approved_by"])
        for j in ctx.rejected:
            t.add_row(j.je_id, j.source, f"[red]{j.description} (REJECTED)[/]", f"${j.amount:,.2f}", "-")
        con.print(t)

        t = Table(title=f"Flux commentary ({ctx.prior} -> {ctx.period})", header_style="bold")
        for c in ("Account", "Prior", "Current", "Var", "%", "Draft explanation"):
            t.add_column(c, overflow="fold")
        for c in ctx.commentary:
            t.add_row(f"{c['account']} {c['name']}", f"{c['prior']:,.0f}", f"{c['current']:,.0f}", f"{c['variance']:+,.0f}",
                      f"{c['pct']:+.1%}", c["explanation"])
        con.print(t)

        con.print(self.checklist())
        status = "[bold green]CLOSE COMPLETE[/]" if report["completion_pct"] >= 100 else "[bold yellow]CLOSE BLOCKED - approvals pending[/]"
        con.print(Panel.fit(f"{status}   {self.progress_bar()}\n"
                            f"{len(report['posted_jes'])} JEs posted | {len(ctx.resolved)} items auto-resolved | "
                            f"{len(ctx.gate.requests)} escalations | {report['pending_approvals']} pending",
                            border_style="green" if report["completion_pct"] >= 100 else "yellow"))
