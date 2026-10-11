"""Generate the synthetic financial environment (SQLite + CSV exports).

Period closed: 2026-09 (M0). Prior period: 2026-08 (M-1).

Deliberate edge cases baked in:
  * unmatched bank fees / interest (no book entry)
  * returned (NSF) item and an unidentified wire (unmatched, need review)
  * batch lockbox deposit (one bank line = two receipts), short-paid wire (within tolerance)
  * outstanding check (book payment not cleared by bank)
  * missing accruals: hosting (high $), legal retainer (duplicated by a manual JE), utilities,
    payroll days after the last pay run
  * missing Sep insurance amortization
  * GL revenue keyed $4,200 below the AR sub-ledger (out-of-balance)
  * revenue / marketing / payroll swings above the $10k flux threshold
  * manual unposted JEs: low value, high value ($48k), duplicate, and unbalanced
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

D = date.fromisoformat

COA = [
    ("1000", "Cash - Operating", "asset"),
    ("1100", "Accounts Receivable", "asset"),
    ("1200", "Prepaid Expenses", "asset"),
    ("2000", "Accounts Payable", "liability"),
    ("2100", "Accrued Expenses", "liability"),
    ("2200", "Accrued Payroll", "liability"),
    ("2900", "Unapplied Cash / Suspense", "liability"),
    ("3000", "Retained Earnings", "equity"),
    ("4000", "Subscription Revenue", "revenue"),
    ("4100", "Services Revenue", "revenue"),
    ("5000", "Hosting & COGS", "expense"),
    ("6000", "Salaries & Wages", "expense"),
    ("6100", "Software & Subscriptions", "expense"),
    ("6200", "Professional Fees", "expense"),
    ("6300", "Marketing", "expense"),
    ("6400", "Rent", "expense"),
    ("6500", "Bank Fees", "expense"),
    ("6600", "Travel & Entertainment", "expense"),
    ("6700", "Insurance", "expense"),
    ("6800", "Utilities", "expense"),
    ("7000", "Interest Income", "revenue"),
]

OPENING_CASH = 1_250_000.00
EMPLOYER_TAX = 0.0765
GL_REVENUE_KEYING_ERROR = 4_200.00  # Stark Sep services invoice keyed 47,800 vs 52,000

# customer, revenue acct, aug amount, sep amount, invoice day, terms
AR_PLAN = [
    ("Globex Corp", "4000", 42_000, 42_000, 3, 30),
    ("Initech", "4000", 38_500, 38_500, 3, 30),
    ("Umbrella Corp", "4000", 55_000, 61_000, 3, 28),
    ("Stark Industries", "4000", 72_000, 72_000, 3, 35),
    ("Wayne Enterprises", "4000", 48_000, 48_000, 3, 30),
    ("Hooli", "4000", 30_000, 30_000, 3, 15),
    ("Pied Piper", "4000", 24_500, 24_500, 3, 15),
    ("Cyberdyne Systems", "4000", 0, 26_000, 3, 15),
    ("Globex Corp", "4100", 20_000, 28_000, 15, 30),
    ("Initech", "4100", 15_000, 15_000, 15, 30),
    ("Stark Industries", "4100", 30_000, 52_000, 15, 30),
    ("Wayne Enterprises", "4100", 20_000, 23_000, 15, 30),
]

# vendor, gl acct, aug amount, sep amount (None = not yet invoiced), invoice day, terms
AP_PLAN = [
    ("Lakeside Properties", "6400", 18_000, 18_000, 1, 0),
    ("CollabSuite Inc", "6100", 7_400, 7_400, 5, 30),
    ("DevTools Co", "6100", 6_200, 6_300, 10, 30),
    ("Hartwell Legal LLP", "6200", 7_500, None, 12, 30),
    ("BrightReach Media", "6300", 22_000, 41_500, 8, 30),
    ("EventPro", "6300", 6_000, 14_000, 15, 30),
    ("Northwind Cloud", "5000", 29_800, None, 28, 25),
    ("City Power & Water", "6800", 2_150, None, 20, 15),
    ("SkyTravel Corp", "6600", 3_900, 4_300, 18, 30),
]

# payroll runs: (id, period_start, period_end, pay_date, gross)
PAYROLL = [
    ("PR-3001", "2026-08-01", "2026-08-14", "2026-08-18", 118_000),
    ("PR-3002", "2026-08-15", "2026-08-28", "2026-09-01", 118_000),
    ("PR-3003", "2026-08-29", "2026-09-11", "2026-09-15", 126_000),
    ("PR-3004", "2026-09-12", "2026-09-25", "2026-09-29", 131_000),
]

PREPAIDS = [
    # id, description, total, start, months, prepaid acct, expense acct, amortized_through
    ("PP-01", "Annual insurance policy", 36_000, "2026-03", 12, "1200", "6700", "2026-08"),
    ("PP-02", "Annual dev-tools license", 24_000, "2026-01", 12, "1200", "6100", "2026-09"),
]

COMMITMENTS = [
    ("CM-01", "Northwind Cloud", "Hosting usage (monthly, billed in arrears)", "5000", 31_000, "2100"),
    ("CM-02", "Hartwell Legal LLP", "Legal retainer", "6200", 7_500, "2100"),
    ("CM-03", "City Power & Water", "Utilities", "6800", 2_150, "2100"),
    ("CM-04", "Lakeside Properties", "Office rent", "6400", 18_000, "2100"),
]

# manual journal entries awaiting posting: (je_id, date, description, preparer, [(acct, dr, cr)])
UNPOSTED_JES = [
    ("UJE-101", "2026-09-30", "Reclass conference travel to marketing", "j.alvarez",
     [("6300", 2_400, 0), ("6600", 0, 2_400)]),
    ("UJE-102", "2026-09-30", "Q3 executive bonus accrual", "m.chen",
     [("6000", 48_000, 0), ("2100", 0, 48_000)]),
    ("UJE-103", "2026-09-30", "Accrue Hartwell Legal Sep retainer", "j.alvarez",
     [("6200", 7_500, 0), ("2100", 0, 7_500)]),
    ("UJE-104", "2026-09-30", "Adjust deferred revenue (draft)", "m.chen",
     [("4000", 5_000, 0), ("2100", 0, 4_500)]),
]

SCHEMA = """
DROP TABLE IF EXISTS chart_of_accounts; DROP TABLE IF EXISTS ap_transactions;
DROP TABLE IF EXISTS ar_transactions; DROP TABLE IF EXISTS payroll_runs;
DROP TABLE IF EXISTS bank_statement; DROP TABLE IF EXISTS unposted_je;
DROP TABLE IF EXISTS gl_trial_balance; DROP TABLE IF EXISTS prepaid_register;
DROP TABLE IF EXISTS recurring_commitments; DROP TABLE IF EXISTS posted_je;
CREATE TABLE chart_of_accounts(account TEXT PRIMARY KEY, name TEXT, type TEXT);
CREATE TABLE ap_transactions(ap_id TEXT PRIMARY KEY, vendor TEXT, invoice_date TEXT, due_date TEXT,
    amount REAL, gl_account TEXT, payment_date TEXT, payment_method TEXT, payment_ref TEXT);
CREATE TABLE ar_transactions(ar_id TEXT PRIMARY KEY, customer TEXT, invoice_date TEXT, amount REAL,
    gl_account TEXT, receipt_date TEXT, receipt_amount REAL);
CREATE TABLE payroll_runs(run_id TEXT PRIMARY KEY, period_start TEXT, period_end TEXT, pay_date TEXT,
    gross REAL, employer_tax REAL, cash_out REAL, gl_account TEXT);
CREATE TABLE bank_statement(bank_id TEXT PRIMARY KEY, txn_date TEXT, description TEXT, amount REAL, balance REAL);
CREATE TABLE unposted_je(je_id TEXT, line_no INTEGER, je_date TEXT, description TEXT, preparer TEXT,
    account TEXT, debit REAL, credit REAL);
CREATE TABLE posted_je(je_id TEXT, line_no INTEGER, je_date TEXT, description TEXT, source TEXT,
    account TEXT, debit REAL, credit REAL, approved_by TEXT);
CREATE TABLE gl_trial_balance(period TEXT, account TEXT, debit REAL, credit REAL,
    PRIMARY KEY(period, account));
CREATE TABLE prepaid_register(prepaid_id TEXT PRIMARY KEY, description TEXT, total_cost REAL,
    start_period TEXT, months INTEGER, prepaid_account TEXT, expense_account TEXT, amortized_through TEXT);
CREATE TABLE recurring_commitments(commitment_id TEXT PRIMARY KEY, vendor TEXT, description TEXT,
    gl_account TEXT, expected_monthly REAL, accrual_account TEXT);
"""


def _add_months(ym: str, n: int) -> str:
    y, m = int(ym[:4]), int(ym[5:])
    t = y * 12 + (m - 1) + n
    return f"{t // 12}-{t % 12 + 1:02d}"


def _build_ar():
    rows, n = [], 2000
    for cust, acct, aug, sep, day, terms in AR_PLAN:
        for amt, ym in ((aug, "2026-08"), (sep, "2026-09")):
            if not amt:
                continue
            n += 1
            inv = D(f"{ym}-{day:02d}")
            rcpt = inv + timedelta(days=terms)
            paid = rcpt <= D(config.PERIOD_END)
            rows.append(dict(ar_id=f"AR-{n}", customer=cust, invoice_date=inv.isoformat(), amount=float(amt),
                             gl_account=acct, receipt_date=rcpt.isoformat() if paid else None,
                             receipt_amount=float(amt) if paid else None))
    # short-paid wire: Wayne Aug subscription arrives $0.35 light (within tolerance)
    for r in rows:
        if r["customer"] == "Wayne Enterprises" and r["gl_account"] == "4000" and r["invoice_date"].startswith("2026-08"):
            r["receipt_amount"] = round(r["amount"] - 0.35, 2)
    return rows


def _build_ap():
    rows, n = [], 1000
    for vendor, acct, aug, sep, day, terms in AP_PLAN:
        for amt, ym in ((aug, "2026-08"), (sep, "2026-09")):
            if amt is None:
                continue
            n += 1
            inv = D(f"{ym}-{day:02d}")
            due = inv + timedelta(days=terms)
            paid = due <= D(config.PERIOD_END)
            method, ref = "ACH", f"ACH{n}"
            pay = due
            # outstanding check: DevTools Aug invoice cut by check on Sep 28, not yet cleared
            if vendor == "DevTools Co" and ym == "2026-08":
                pay, method, ref, paid = D("2026-09-28"), "CHECK", "CHK10442", True
            rows.append(dict(ap_id=f"AP-{n}", vendor=vendor, invoice_date=inv.isoformat(), due_date=due.isoformat(),
                             amount=float(amt), gl_account=acct,
                             payment_date=pay.isoformat() if paid else None,
                             payment_method=method if paid else None, payment_ref=ref if paid else None))
    return rows


def _build_payroll():
    out = []
    for rid, ps, pe, pay, gross in PAYROLL:
        tax = round(gross * EMPLOYER_TAX, 2)
        out.append(dict(run_id=rid, period_start=ps, period_end=pe, pay_date=pay, gross=float(gross),
                        employer_tax=tax, cash_out=round(gross + tax, 2), gl_account="6000"))
    return out


def _build_bank(ar, ap, payroll):
    """Bank lines derived from book cash for Sep, plus deliberate exceptions."""
    p0, p1 = config.PERIOD + "-01", config.PERIOD_END
    lines = []  # (date, desc, amount)

    # batch lockbox deposit: Hooli + Pied Piper Sep receipts land as one deposit
    batch = [r for r in ar if r["customer"] in ("Hooli", "Pied Piper") and r["receipt_date"]
             and p0 <= r["receipt_date"] <= p1]
    if batch:
        lines.append((max(r["receipt_date"] for r in batch), "LOCKBOX DEPOSIT HOOLI/PIED PIPER",
                      round(sum(r["receipt_amount"] for r in batch), 2)))
    for r in ar:
        if r in batch or not r["receipt_date"] or not (p0 <= r["receipt_date"] <= p1):
            continue
        d = D(r["receipt_date"]) + timedelta(days=1)
        lines.append((d.isoformat(), f"WIRE IN {r['customer'].upper()} {r['ar_id']}", r["receipt_amount"]))
    for r in ap:
        if not r["payment_date"] or not (p0 <= r["payment_date"] <= p1) or r["payment_method"] == "CHECK":
            continue
        d = D(r["payment_date"]) + timedelta(days=1 if r["payment_method"] == "ACH" else 0)
        lines.append((d.isoformat(), f"ACH PMT {r['vendor'].upper()} {r['payment_ref']}", -r["amount"]))
    for r in payroll:
        if p0 <= r["pay_date"] <= p1:
            lines.append((r["pay_date"], f"PAYROLL DEBIT {r['run_id']}", -r["cash_out"]))

    # deliberate unmatched bank items
    lines += [
        ("2026-09-09", "WIRE FEE", -25.00), ("2026-09-17", "WIRE FEE", -25.00),
        ("2026-09-22", "WIRE FEE", -25.00), ("2026-09-30", "MONTHLY SERVICE FEE", -85.00),
        ("2026-09-30", "INTEREST EARNED", 61.37),
        ("2026-09-21", "RETURNED ITEM NSF CHK 8812 UMBRELLA CORP", -3_750.00),
        ("2026-09-24", "INCOMING WIRE UNIDENTIFIED ORIG", 12_480.00),
    ]
    lines.sort(key=lambda x: (x[0], x[1]))
    bal, out = OPENING_CASH, []
    for i, (d, desc, amt) in enumerate(lines, 1):
        bal = round(bal + amt, 2)
        out.append(dict(bank_id=f"BK-{i:03d}", txn_date=d, description=desc, amount=round(amt, 2), balance=bal))
    return out


def _book_cash_total(ar, ap, payroll):
    p0, p1 = config.PERIOD + "-01", config.PERIOD_END
    t = sum(r["amount"] for r in ar if r["receipt_date"] and p0 <= r["receipt_date"] <= p1)  # books record invoice amount
    t -= sum(r["amount"] for r in ap if r["payment_date"] and p0 <= r["payment_date"] <= p1)
    t -= sum(r["cash_out"] for r in payroll if p0 <= r["pay_date"] <= p1)
    return round(t, 2)


def _build_tb(ar, ap, payroll):
    """P&L activity for both periods; balance-sheet balances for M0. Debit/credit columns."""
    tb = {}  # (period, account) -> signed (debit - credit)

    def add(period, acct, signed):
        tb[(period, acct)] = round(tb.get((period, acct), 0.0) + signed, 2)

    for per in (config.PRIOR_PERIOD, config.PERIOD):
        for r in ar:
            if r["invoice_date"].startswith(per):
                amt = r["amount"]
                if per == config.PERIOD and r["customer"] == "Stark Industries" and r["gl_account"] == "4100":
                    amt -= GL_REVENUE_KEYING_ERROR
                add(per, r["gl_account"], -amt)
        for r in ap:
            if r["invoice_date"].startswith(per):
                add(per, r["gl_account"], r["amount"])
        for r in payroll:
            if r["period_end"].startswith(per):
                add(per, "6000", r["gross"] + r["employer_tax"])
        add(per, "6100", 2_000)                       # software license amortization (posted both months)
    add(config.PRIOR_PERIOD, "6700", 3_000)           # insurance amortization posted in Aug, MISSING in Sep
    add(config.PRIOR_PERIOD, "6500", 110.00)
    add(config.PRIOR_PERIOD, "7000", -55.00)

    p0, p1 = config.PERIOD + "-01", config.PERIOD_END
    per = config.PERIOD
    add(per, "1000", OPENING_CASH + _book_cash_total(ar, ap, payroll))
    open_ar = sum(r["amount"] for r in ar if not r["receipt_date"] or r["receipt_date"] > p1)
    add(per, "1100", open_ar - GL_REVENUE_KEYING_ERROR)
    add(per, "1200", 18_000 + 6_000)                  # insurance 36k-6*3k, license 24k-9*2k
    add(per, "2000", -sum(r["amount"] for r in ap if not r["payment_date"] or r["payment_date"] > p1))
    # equity plug so the TB balances
    net = sum(v for (p, a), v in tb.items() if p == per)
    add(per, "3000", -net)
    return tb


def build(db_path: Path = config.DB_PATH, csv_dir: Path | None = config.CSV_DIR) -> Path:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    ar, ap, payroll = _build_ar(), _build_ap(), _build_payroll()
    bank = _build_bank(ar, ap, payroll)
    tb = _build_tb(ar, ap, payroll)

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    tables = {
        "chart_of_accounts": [dict(account=a, name=n, type=t) for a, n, t in COA],
        "ap_transactions": ap, "ar_transactions": ar, "payroll_runs": payroll, "bank_statement": bank,
        "unposted_je": [dict(je_id=j, line_no=i, je_date=d, description=desc, preparer=p, account=a,
                             debit=float(dr), credit=float(cr))
                        for j, d, desc, p, lines in UNPOSTED_JES for i, (a, dr, cr) in enumerate(lines, 1)],
        "gl_trial_balance": [dict(period=p, account=a, debit=max(v, 0.0), credit=max(-v, 0.0))
                             for (p, a), v in sorted(tb.items())],
        "prepaid_register": [dict(zip(("prepaid_id", "description", "total_cost", "start_period", "months",
                                       "prepaid_account", "expense_account", "amortized_through"), r))
                             for r in PREPAIDS],
        "recurring_commitments": [dict(zip(("commitment_id", "vendor", "description", "gl_account",
                                            "expected_monthly", "accrual_account"), r)) for r in COMMITMENTS],
    }
    for name, rows in tables.items():
        cols = list(rows[0].keys())
        conn.executemany(f"INSERT INTO {name}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                         [tuple(r[c] for c in cols) for r in rows])
        if csv_dir:
            Path(csv_dir).mkdir(parents=True, exist_ok=True)
            with open(Path(csv_dir) / f"{name}.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                w.writerows(rows)
    conn.commit()
    conn.close()
    return db_path


if __name__ == "__main__":
    print(f"Generated {build()}")
