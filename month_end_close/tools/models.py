"""Shared data models for tools and agents."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class JELine:
    account: str
    debit: float = 0.0
    credit: float = 0.0


@dataclass
class JournalEntry:
    je_id: str
    date: str
    description: str
    lines: list[JELine]
    source: str = "manual"          # manual | accrual | amortization | recon | tieout
    rationale: str = ""
    preparer: str = "agent"
    status: str = "draft"           # draft | approved | rejected | posted

    @property
    def total_debits(self) -> float:
        return round(sum(l.debit for l in self.lines), 2)

    @property
    def total_credits(self) -> float:
        return round(sum(l.credit for l in self.lines), 2)

    @property
    def is_balanced(self) -> bool:
        return abs(self.total_debits - self.total_credits) < 0.005

    @property
    def amount(self) -> float:
        return max(self.total_debits, self.total_credits)

    @staticmethod
    def simple(je_id, date, description, debit_acct, credit_acct, amount, **kw) -> "JournalEntry":
        amount = round(amount, 2)
        return JournalEntry(je_id, date, description,
                            [JELine(debit_acct, debit=amount), JELine(credit_acct, credit=amount)], **kw)
