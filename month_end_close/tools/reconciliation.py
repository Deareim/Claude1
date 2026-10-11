"""Bank and sub-ledger reconciliation tool."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations

import pandas as pd

import config
from tools.ledger import read

STOPWORDS = {"WIRE", "IN", "ACH", "PMT", "PAYROLL", "DEBIT", "DEPOSIT", "LOCKBOX", "INC", "LLC", "CORP", "CO"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[A-Z0-9]+", s.upper()) if t not in STOPWORDS and len(t) > 2}


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


@dataclass
class Match:
    bank_ids: list[str]
    book_ids: list[str]
    kind: str            # exact | tolerance | batch
    bank_amount: float
    book_amount: float

    @property
    def difference(self) -> float:
        return round(self.bank_amount - self.book_amount, 2)


@dataclass
class ReconResult:
    matches: list[Match] = field(default_factory=list)
    unmatched_bank: pd.DataFrame = None
    unmatched_book: pd.DataFrame = None


def build_book_cash(conn, period: str) -> pd.DataFrame:
    """Cash movements per the books for the period (AR receipts, AP payments, payroll)."""
    lo, hi = f"{period}-01", f"{period}-31"
    ar = read(conn, """SELECT ar_id AS book_id, receipt_date AS txn_date, amount AS amount,
                       customer || ' ' || ar_id AS description, 'AR' AS source FROM ar_transactions
                       WHERE receipt_date BETWEEN ? AND ?""", (lo, hi))
    ap = read(conn, """SELECT ap_id AS book_id, payment_date AS txn_date, -amount AS amount,
                       vendor || ' ' || payment_ref AS description, 'AP' AS source FROM ap_transactions
                       WHERE payment_date BETWEEN ? AND ?""", (lo, hi))
    pr = read(conn, """SELECT run_id AS book_id, pay_date AS txn_date, -cash_out AS amount,
                       'PAYROLL ' || run_id AS description, 'PAYROLL' AS source FROM payroll_runs
                       WHERE pay_date BETWEEN ? AND ?""", (lo, hi))
    return pd.concat([ar, ap, pr], ignore_index=True).sort_values("txn_date").reset_index(drop=True)


def load_bank(conn, period: str) -> pd.DataFrame:
    return read(conn, "SELECT bank_id, txn_date, description, amount FROM bank_statement "
                      "WHERE txn_date BETWEEN ? AND ? ORDER BY txn_date", (f"{period}-01", f"{period}-31"))


def match_transactions(bank: pd.DataFrame, book: pd.DataFrame, amount_tol: float = config.BANK_AMOUNT_TOL,
                       date_window: int = config.BANK_DATE_WINDOW) -> ReconResult:
    """Pass 1: one-to-one within amount/date tolerance (best score first). Pass 2: many book -> one bank."""
    bank_rows = bank.to_dict("records")
    book_rows = book.to_dict("records")
    used_bank, used_book, matches = set(), set(), []

    cands = []
    for b in bank_rows:
        for k in book_rows:
            if (b["amount"] > 0) != (k["amount"] > 0):
                continue
            diff = abs(b["amount"] - k["amount"])
            dd = _days(b["txn_date"], k["txn_date"])
            if diff <= amount_tol and dd <= date_window:
                overlap = len(_tokens(b["description"]) & _tokens(k["description"]))
                cands.append((round(diff, 2), -overlap, dd, b["bank_id"], k["book_id"], b, k))
    cands.sort(key=lambda c: c[:5])
    for diff, _, _, bid, kid, b, k in cands:
        if bid in used_bank or kid in used_book:
            continue
        used_bank.add(bid)
        used_book.add(kid)
        matches.append(Match([bid], [kid], "exact" if diff < 0.005 else "tolerance", b["amount"], k["amount"]))

    # pass 2: batch deposits / combined payments (2-3 book items sum to one bank line)
    for b in bank_rows:
        if b["bank_id"] in used_bank:
            continue
        pool = [k for k in book_rows if k["book_id"] not in used_book and (k["amount"] > 0) == (b["amount"] > 0)
                and _days(b["txn_date"], k["txn_date"]) <= date_window][:25]
        found = None
        for n in (2, 3):
            for combo in combinations(pool, n):
                if abs(sum(c["amount"] for c in combo) - b["amount"]) <= amount_tol:
                    found = combo
                    break
            if found:
                break
        if found:
            used_bank.add(b["bank_id"])
            used_book.update(c["book_id"] for c in found)
            matches.append(Match([b["bank_id"]], [c["book_id"] for c in found], "batch", b["amount"],
                                 round(sum(c["amount"] for c in found), 2)))

    return ReconResult(
        matches=matches,
        unmatched_bank=bank[~bank["bank_id"].isin(used_bank)].reset_index(drop=True),
        unmatched_book=book[~book["book_id"].isin(used_book)].reset_index(drop=True),
    )


def classify_unmatched_bank(description: str, amount: float) -> str:
    d = description.upper()
    if "FEE" in d or "CHARGE" in d:
        return "bank_fee"
    if "INTEREST" in d and amount > 0:
        return "interest"
    if "RETURN" in d or "NSF" in d:
        return "returned_item"
    return "unidentified"


def classify_unmatched_book(txn_date: str, period_end: str = config.PERIOD_END) -> str:
    """Book items recorded close to month-end are timing differences."""
    return "timing" if _days(txn_date, period_end) <= config.OUTSTANDING_WINDOW else "investigate"


def bank_rec_summary(conn, period: str, result: ReconResult, bank: pd.DataFrame, book: pd.DataFrame) -> dict:
    """Classic four-way proof: adjusted bank balance must equal adjusted book balance."""
    opening = conn.execute("SELECT balance - amount FROM bank_statement ORDER BY txn_date, bank_id LIMIT 1").fetchone()[0]
    bank_end = round(opening + bank["amount"].sum(), 2)
    book_end = round(opening + book["amount"].sum(), 2)
    ub, uk = result.unmatched_bank, result.unmatched_book
    tol_adj = round(sum(m.difference for m in result.matches), 2)
    adj_bank = round(bank_end + uk["amount"].sum(), 2)          # +deposits in transit, -outstanding payments
    adj_book = round(book_end + ub["amount"].sum() + tol_adj, 2)  # +unrecorded bank items, +tolerance diffs
    return dict(opening=opening, bank_end=bank_end, book_end=book_end, outstanding_net=round(uk["amount"].sum(), 2),
                unrecorded_bank_net=round(ub["amount"].sum(), 2), tolerance_diffs=tol_adj,
                adjusted_bank=adj_bank, adjusted_book=adj_book, difference=round(adj_bank - adj_book, 2))


@dataclass
class TieOut:
    account: str
    name: str
    subledger: float
    gl: float

    @property
    def diff(self) -> float:
        return round(self.subledger - self.gl, 2)

    @property
    def ok(self) -> bool:
        return abs(self.diff) <= config.SUBLEDGER_TOL


def subledger_tieout(conn, period: str) -> list[TieOut]:
    """Compare sub-ledger totals to GL control/activity accounts."""
    from tools.ledger import trial_balance
    tb = trial_balance(conn, period).set_index("account")
    gl = lambda a: float(tb.loc[a, "balance"]) if a in tb.index else 0.0  # noqa: E731
    end = config.PERIOD_END
    q = lambda sql, *p: float(conn.execute(sql, p).fetchone()[0] or 0)  # noqa: E731
    out = []
    for acct, name in (("4000", "Subscription Revenue"), ("4100", "Services Revenue")):
        out.append(TieOut(acct, f"{name} vs AR invoicing",
                          q("SELECT SUM(amount) FROM ar_transactions WHERE gl_account=? AND invoice_date LIKE ?",
                            acct, f"{period}%"), gl(acct)))
    out.append(TieOut("1100", "AR control vs open invoices",
                      q("SELECT SUM(amount) FROM ar_transactions WHERE receipt_date IS NULL OR receipt_date>?", end),
                      gl("1100")))
    out.append(TieOut("2000", "AP control vs open invoices",
                      q("SELECT SUM(amount) FROM ap_transactions WHERE payment_date IS NULL OR payment_date>?", end),
                      gl("2000")))
    out.append(TieOut("6000", "Salaries vs payroll runs",
                      q("SELECT SUM(gross+employer_tax) FROM payroll_runs WHERE period_end LIKE ?", f"{period}%"),
                      gl("6000")))
    return out
