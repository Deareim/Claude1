"""Event bus, shared close context and base agent."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import config
from tools.models import JournalEntry


@dataclass
class Event:
    ts: float
    agent: str
    kind: str        # step_start step_end reasoning action resolved escalated approval info error
    message: str
    step: str | None = None
    data: dict = field(default_factory=dict)


class EventBus:
    def __init__(self):
        self.events: list[Event] = []
        self._subs: list[Callable[[Event], None]] = []
        self.current_step: str | None = None

    def subscribe(self, fn: Callable[[Event], None]):
        self._subs.append(fn)

    def emit(self, agent: str, kind: str, message: str, **data: Any) -> Event:
        ev = Event(time.time(), agent, kind, message, self.current_step, data)
        self.events.append(ev)
        for fn in self._subs:
            fn(ev)
        return ev


@dataclass
class CloseContext:
    conn: Any
    bus: EventBus
    gate: Any
    period: str = config.PERIOD
    prior: str = config.PRIOR_PERIOD
    approved: list[tuple[JournalEntry, str]] = field(default_factory=list)
    rejected: list[JournalEntry] = field(default_factory=list)
    resolved: list[dict] = field(default_factory=list)     # auto-resolved items
    commentary: list[dict] = field(default_factory=list)   # variance explanations
    metrics: dict = field(default_factory=dict)


class BaseAgent:
    name = "Agent"

    def __init__(self, ctx: CloseContext):
        self.ctx = ctx

    # --- logging helpers -------------------------------------------------
    def think(self, msg: str, **d):
        self.ctx.bus.emit(self.name, "reasoning", msg, **d)

    def act(self, msg: str, **d):
        self.ctx.bus.emit(self.name, "action", msg, **d)

    def resolve(self, category: str, description: str, amount: float = 0.0, je_id: str | None = None):
        item = dict(agent=self.name, category=category, description=description, amount=amount, je_id=je_id,
                    step=self.ctx.bus.current_step)
        self.ctx.resolved.append(item)
        self.ctx.bus.emit(self.name, "resolved", description, amount=amount, category=category)

    # --- JE routing through the HITL safety gate -------------------------
    def submit_je(self, je: JournalEntry, category: str = "journal_entry", force_reason: str | None = None) -> bool:
        """Auto-approve low-risk balanced JEs; escalate everything else. True if approved."""
        gate = self.ctx.gate
        reasons = gate.je_review_reasons(je)
        if force_reason:
            reasons.insert(0, force_reason)
        if not reasons:
            je.status = "approved"
            self.ctx.approved.append((je, "auto-policy"))
            self.resolve(category, f"{je.je_id} {je.description} - auto-approved (below ${config.HITL_JE_THRESHOLD:,.0f})",
                         je.amount, je.je_id)
            return True
        if not je.is_balanced and category == "journal_entry":
            category = "unbalanced_je"
        req = gate.submit(requested_by=self.name, category=category, title=f"{je.je_id}: {je.description}",
                          amount=je.amount, reason="; ".join(reasons), je=je)
        if req.status == "approved":
            je.status = "approved"
            self.ctx.approved.append((je, req.decided_by))
        elif req.status == "rejected":
            je.status = "rejected"
            self.ctx.rejected.append(je)
        return req.status == "approved"
