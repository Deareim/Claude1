"""Accrual & amortization calculator: detects missing month-end entries and drafts JEs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import config
from tools.ledger import load_unposted_jes, read
from tools.models import JournalEntry


@dataclass
class Finding:
    kind: str                 # vendor_accrual | payroll_accrual | amortization
    subject: str
    amount: float
    je: JournalEntry | None
    evidence: str
    duplicate_of: str | None = None   # existing unposted JE that already covers this


def _add_months(ym: str, n: int) -> str:
    t = int(ym[:4]) * 12 + int(ym[5:]) - 1 + n
    return f"{t // 12}-{t % 12 + 1:02d}"


def amortization_due(total: float, months: int) -> float:
    return round(total / months, 2)


def working_days_between(start: date, end: date) -> int:
    """Mon-Fri days in (start, end]."""
    d, n = start, 0
    while d < end:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def _find_duplicate(unposted: list[JournalEntry], account: str, amount: float, tol: float = 0.05) -> str | None:
    for je in unposted:
        for l in je.lines:
            if l.account == account and l.debit and abs(l.debit - amount) <= amount * tol:
                return je.je_id
    return None


def detect_missing_vendor_accruals(conn, period: str, seq_start: int = 1) -> list[Finding]:
    unposted = load_unposted_jes(conn)
    findings, seq = [], seq_start
    for c in read(conn, "SELECT * FROM recurring_commitments").itertuples():
        n = conn.execute("SELECT COUNT(*) FROM ap_transactions WHERE vendor=? AND invoice_date LIKE ?",
                         (c.vendor, f"{period}%")).fetchone()[0]
        if n:
            continue
        last = conn.execute("SELECT amount, invoice_date FROM ap_transactions WHERE vendor=? "
                            "ORDER BY invoice_date DESC LIMIT 1", (c.vendor,)).fetchone()
        evidence = (f"No {period} invoice from {c.vendor}; last invoice ${last[0]:,.2f} on {last[1]}"
                    if last else f"No invoices from {c.vendor}")
        je = JournalEntry.simple(f"ACR-{seq:03d}", config.PERIOD_END, f"Accrue {c.vendor} - {c.description} ({period})",
                                 c.gl_account, c.accrual_account, c.expected_monthly, source="accrual",
                                 rationale=f"{evidence}; contract run-rate ${c.expected_monthly:,.2f}/month")
        seq += 1
        findings.append(Finding("vendor_accrual", c.vendor, c.expected_monthly, je, evidence,
                                _find_duplicate(unposted, c.gl_account, c.expected_monthly)))
    return findings


def detect_payroll_accrual(conn, period: str, seq_start: int = 50) -> list[Finding]:
    row = conn.execute("SELECT run_id, period_end, gross, employer_tax FROM payroll_runs "
                       "ORDER BY period_end DESC LIMIT 1").fetchone()
    last_end = date.fromisoformat(row[1])
    pe = date.fromisoformat(config.PERIOD_END)
    unpaid = working_days_between(last_end, pe)
    if unpaid <= 0:
        return []
    run_total = row[2] + row[3]
    amt = round(run_total * unpaid / 10, 2)  # biweekly cycle = 10 working days
    ev = f"Last payroll period ended {row[1]} ({row[0]}); {unpaid} unpaid working days to {config.PERIOD_END}"
    je = JournalEntry.simple(f"ACR-{seq_start:03d}", config.PERIOD_END, "Accrue unpaid payroll at month-end",
                             "6000", "2200", amt, source="accrual",
                             rationale=f"{ev}; {unpaid}/10 x ${run_total:,.2f} fully-loaded run cost")
    return [Finding("payroll_accrual", "Payroll", amt, je, ev,
                    _find_duplicate(load_unposted_jes(conn), "6000", amt))]


def detect_missing_amortization(conn, period: str, seq_start: int = 80) -> list[Finding]:
    findings, seq = [], seq_start
    for p in read(conn, "SELECT * FROM prepaid_register").itertuples():
        end_period = _add_months(p.start_period, p.months - 1)
        if p.amortized_through >= period or period > end_period:
            continue
        monthly = amortization_due(p.total_cost, p.months)
        missed = 0
        cur = p.amortized_through
        while cur < period:
            cur = _add_months(cur, 1)
            missed += 1
        amt = round(monthly * missed, 2)
        ev = f"{p.description}: amortized through {p.amortized_through}, {missed} month(s) behind at ${monthly:,.2f}/month"
        je = JournalEntry.simple(f"AMT-{seq:03d}", config.PERIOD_END, f"Amortize {p.description} ({period})",
                                 p.expense_account, p.prepaid_account, amt, source="amortization",
                                 rationale=f"{ev}; straight-line over {p.months} months from {p.start_period}")
        seq += 1
        findings.append(Finding("amortization", p.description, amt, je, ev,
                                _find_duplicate(load_unposted_jes(conn), p.expense_account, amt)))
    return findings


def run_all(conn, period: str = config.PERIOD) -> list[Finding]:
    return (detect_missing_vendor_accruals(conn, period) + detect_payroll_accrual(conn, period)
            + detect_missing_amortization(conn, period))
