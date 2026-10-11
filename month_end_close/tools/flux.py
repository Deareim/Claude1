"""Flux analysis engine: M-1 vs M0 variance detection and driver analysis."""
from __future__ import annotations

import pandas as pd

import config
from tools.ledger import read, trial_balance


def run_flux(conn, prior: str = config.PRIOR_PERIOD, current: str = config.PERIOD,
             pct_threshold: float = config.FLUX_PCT, abs_threshold: float = config.FLUX_ABS,
             pct_min_abs: float = config.FLUX_PCT_MIN_ABS) -> pd.DataFrame:
    """Flag accounts present in both periods whose change breaches either threshold.

    Rule: |var| >= abs_threshold, OR (|pct| >= pct_threshold AND |var| >= pct_min_abs) - the floor avoids
    flagging immaterial swings like $50 -> $60.
    """
    p, c = trial_balance(conn, prior), trial_balance(conn, current)
    df = p[["account", "name", "type", "balance"]].merge(c[["account", "balance"]], on="account", how="outer",
                                                         suffixes=("_prior", "_current"))
    meta = read(conn, "SELECT account, name AS n2, type AS t2 FROM chart_of_accounts")
    df = df.merge(meta, on="account", how="left")
    df["name"] = df["name"].fillna(df["n2"])
    df["type"] = df["type"].fillna(df["t2"])
    df = df.drop(columns=["n2", "t2"])
    df = df[df["type"].isin(["revenue", "expense"])].copy()      # P&L accounts exist in both periods
    df = df.rename(columns={"balance_prior": "prior", "balance_current": "current"}).fillna({"prior": 0.0, "current": 0.0})
    df["variance"] = (df["current"] - df["prior"]).round(2)
    df["pct"] = df.apply(lambda r: (r["variance"] / abs(r["prior"])) if r["prior"] else (1.0 if r["variance"] else 0.0),
                         axis=1)
    over_abs = df["variance"].abs() >= abs_threshold
    over_pct = (df["pct"].abs() >= pct_threshold) & (df["variance"].abs() >= pct_min_abs)
    df["flagged"] = over_abs | over_pct
    df["reason"] = ""
    df.loc[over_abs, "reason"] = f">= ${abs_threshold:,.0f}"
    df.loc[over_pct & ~over_abs, "reason"] = f">= {pct_threshold:.0%}"
    df.loc[over_pct & over_abs, "reason"] = f">= ${abs_threshold:,.0f} and >= {pct_threshold:.0%}"
    return df.sort_values("variance", key=abs, ascending=False).reset_index(drop=True)


def driver_analysis(conn, account: str, prior: str, current: str) -> list[dict]:
    """Identify counterparties (customers/vendors/payroll runs/JEs) driving the movement on an account."""
    if account in ("4000", "4100"):
        sql, sign = ("SELECT customer AS who, substr(invoice_date,1,7) AS per, SUM(amount) AS amt FROM ar_transactions "
                     "WHERE gl_account=? GROUP BY who, per"), 1
    elif account == "6000":
        sql, sign = ("SELECT 'Payroll runs' AS who, substr(period_end,1,7) AS per, SUM(gross+employer_tax) AS amt "
                     "FROM payroll_runs WHERE gl_account=? GROUP BY per"), 1
    else:
        sql, sign = ("SELECT vendor AS who, substr(invoice_date,1,7) AS per, SUM(amount) AS amt FROM ap_transactions "
                     "WHERE gl_account=? GROUP BY who, per"), 1
    df = read(conn, sql, (account,))
    piv = df.pivot_table(index="who", columns="per", values="amt", aggfunc="sum", fill_value=0.0)
    for per in (prior, current):
        if per not in piv.columns:
            piv[per] = 0.0
    piv["delta"] = piv[current] - piv[prior]
    drivers = [dict(driver=w, prior=float(r[prior]), current=float(r[current]), delta=round(float(r["delta"]), 2))
               for w, r in piv.sort_values("delta", key=abs, ascending=False).iterrows() if abs(r["delta"]) >= 0.005]
    # approved JEs posted this close that touch the account
    for j in read(conn, """SELECT je_id, description, SUM(debit - credit) AS net FROM posted_je
                           WHERE account=? AND source != 'tieout' GROUP BY je_id, description""", (account,)).itertuples():
        t = conn.execute("SELECT type FROM chart_of_accounts WHERE account=?", (account,)).fetchone()[0]
        delta = j.net if t == "expense" else -j.net
        drivers.append(dict(driver=f"{j.je_id}: {j.description}", prior=0.0, current=float(delta), delta=round(float(delta), 2)))
    drivers.sort(key=lambda d: abs(d["delta"]), reverse=True)
    return drivers
