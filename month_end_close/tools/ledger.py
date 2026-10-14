"""GL helpers: connection, trial balance, unposted JEs, posting."""
from __future__ import annotations

import sqlite3

import pandas as pd

import config
from tools.models import JELine, JournalEntry


def connect(path=config.DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def read(conn, sql: str, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def chart_of_accounts(conn) -> pd.DataFrame:
    return read(conn, "SELECT * FROM chart_of_accounts").set_index("account")


def trial_balance(conn, period: str) -> pd.DataFrame:
    """Rows: account, name, type, debit, credit, balance (natural-sign)."""
    df = read(conn, """SELECT t.account, c.name, c.type, t.debit, t.credit FROM gl_trial_balance t
                       JOIN chart_of_accounts c USING(account) WHERE period=? ORDER BY t.account""", (period,))
    debit_normal = df["type"].isin(["asset", "expense"])
    df["balance"] = (df["debit"] - df["credit"]).where(debit_normal, df["credit"] - df["debit"]).round(2)
    return df


def tb_totals(conn, period: str) -> tuple[float, float]:
    r = conn.execute("SELECT COALESCE(SUM(debit),0), COALESCE(SUM(credit),0) FROM gl_trial_balance WHERE period=?",
                     (period,)).fetchone()
    return round(r[0], 2), round(r[1], 2)


def tb_is_balanced(conn, period: str) -> bool:
    d, c = tb_totals(conn, period)
    return abs(d - c) < 0.005


def load_unposted_jes(conn) -> list[JournalEntry]:
    df = read(conn, "SELECT * FROM unposted_je ORDER BY je_id, line_no")
    jes = []
    for je_id, g in df.groupby("je_id", sort=True):
        first = g.iloc[0]
        jes.append(JournalEntry(je_id, first.je_date, first.description,
                                [JELine(r.account, r.debit, r.credit) for r in g.itertuples()],
                                source="manual", preparer=first.preparer))
    return jes


def post_journal_entry(conn, je: JournalEntry, period: str, approved_by: str) -> None:
    """Post a balanced JE to the GL trial balance for `period` and record it in posted_je."""
    if not je.is_balanced:
        raise ValueError(f"{je.je_id} is out of balance: Dr {je.total_debits} vs Cr {je.total_credits}")
    known = {r[0] for r in conn.execute("SELECT account FROM chart_of_accounts")}
    for l in je.lines:
        if l.account not in known:
            raise ValueError(f"{je.je_id}: unknown account {l.account}")
    for i, l in enumerate(je.lines, 1):
        conn.execute("INSERT INTO posted_je VALUES (?,?,?,?,?,?,?,?,?)",
                     (je.je_id, i, je.date, je.description, je.source, l.account, l.debit, l.credit, approved_by))
        conn.execute("INSERT OR IGNORE INTO gl_trial_balance(period, account, debit, credit) VALUES (?,?,0,0)",
                     (period, l.account))
        conn.execute("UPDATE gl_trial_balance SET debit = ROUND(debit + ?, 2), credit = ROUND(credit + ?, 2) "
                     "WHERE period=? AND account=?", (l.debit, l.credit, period, l.account))
        # keep each account on one side only (net the debit/credit columns)
        conn.execute("""UPDATE gl_trial_balance SET debit = MAX(debit - credit, 0), credit = MAX(credit - debit, 0)
                        WHERE period=? AND account=?""", (period, l.account))
    je.status = "posted"
    conn.commit()
