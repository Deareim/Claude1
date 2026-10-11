import pandas as pd

from tools import reconciliation as rec


def test_exact_and_tolerance_match():
    bank = pd.DataFrame([dict(bank_id="B1", txn_date="2026-09-02", description="WIRE IN ACME", amount=100.00),
                         dict(bank_id="B2", txn_date="2026-09-03", description="WIRE IN BETA", amount=199.65)])
    book = pd.DataFrame([dict(book_id="K1", txn_date="2026-09-01", description="ACME", amount=100.00),
                         dict(book_id="K2", txn_date="2026-09-03", description="BETA", amount=200.00)])
    res = rec.match_transactions(bank, book)
    assert {m.kind for m in res.matches} == {"exact", "tolerance"}
    assert res.unmatched_bank.empty and res.unmatched_book.empty


def test_amount_outside_tolerance_not_matched():
    bank = pd.DataFrame([dict(bank_id="B1", txn_date="2026-09-02", description="X", amount=100.00)])
    book = pd.DataFrame([dict(book_id="K1", txn_date="2026-09-02", description="X", amount=102.50)])
    assert len(rec.match_transactions(bank, book).matches) == 0


def test_date_window_enforced():
    bank = pd.DataFrame([dict(bank_id="B1", txn_date="2026-09-20", description="X", amount=50.0)])
    book = pd.DataFrame([dict(book_id="K1", txn_date="2026-09-01", description="X", amount=50.0)])
    assert len(rec.match_transactions(bank, book).matches) == 0


def test_batch_deposit():
    bank = pd.DataFrame([dict(bank_id="B1", txn_date="2026-09-18", description="LOCKBOX", amount=300.0)])
    book = pd.DataFrame([dict(book_id="K1", txn_date="2026-09-18", description="A", amount=100.0),
                         dict(book_id="K2", txn_date="2026-09-18", description="B", amount=200.0)])
    res = rec.match_transactions(bank, book)
    assert res.matches[0].kind == "batch" and len(res.matches[0].book_ids) == 2


def test_sign_must_agree():
    bank = pd.DataFrame([dict(bank_id="B1", txn_date="2026-09-02", description="X", amount=-100.0)])
    book = pd.DataFrame([dict(book_id="K1", txn_date="2026-09-02", description="X", amount=100.0)])
    assert len(rec.match_transactions(bank, book).matches) == 0


def test_classification():
    assert rec.classify_unmatched_bank("MONTHLY SERVICE FEE", -85) == "bank_fee"
    assert rec.classify_unmatched_bank("INTEREST EARNED", 5) == "interest"
    assert rec.classify_unmatched_bank("RETURNED ITEM NSF", -5) == "returned_item"
    assert rec.classify_unmatched_bank("WIRE UNIDENTIFIED", 5) == "unidentified"


def test_demo_data_bank_rec_proves(conn):
    bank, book = rec.load_bank(conn, "2026-09"), rec.build_book_cash(conn, "2026-09")
    res = rec.match_transactions(bank, book)
    s = rec.bank_rec_summary(conn, "2026-09", res, bank, book)
    assert abs(s["difference"]) < 0.005
    assert len(res.unmatched_bank) == 7 and len(res.unmatched_book) == 1


def test_tieout_finds_revenue_gap(conn):
    bad = [t for t in rec.subledger_tieout(conn, "2026-09") if not t.ok]
    assert {t.account for t in bad} == {"4100", "1100"}
    assert all(abs(t.diff - 4200) < 0.01 for t in bad)
