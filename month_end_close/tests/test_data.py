from tools import ledger


def test_tables_populated(conn):
    for t in ("ap_transactions", "ar_transactions", "payroll_runs", "bank_statement", "unposted_je",
              "gl_trial_balance", "chart_of_accounts"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] > 0


def test_opening_tb_balances(conn):
    assert ledger.tb_is_balanced(conn, "2026-09")


def test_bank_balance_chain(conn):
    last = conn.execute("SELECT balance FROM bank_statement ORDER BY bank_id DESC LIMIT 1").fetchone()[0]
    total = conn.execute("SELECT 1250000 + SUM(amount) FROM bank_statement").fetchone()[0]
    assert abs(last - total) < 0.01


def test_unposted_includes_unbalanced_entry(conn):
    assert any(not je.is_balanced for je in ledger.load_unposted_jes(conn))
