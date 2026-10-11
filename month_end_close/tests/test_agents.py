from agents.coordinator import CloseCoordinator
from agents.hitl import HITLGate
from agents.base import EventBus
from tools import ledger
from tools.models import JournalEntry


def test_gate_reasons():
    big = JournalEntry.simple("X", "2026-09-30", "big", "6000", "2100", 30_000)
    small = JournalEntry.simple("Y", "2026-09-30", "small", "6000", "2100", 500)
    assert HITLGate.je_review_reasons(big) and not HITLGate.je_review_reasons(small)
    unbal = JournalEntry("Z", "d", "unbalanced", [])
    unbal.lines = JournalEntry.simple("Z", "d", "x", "6000", "2100", 10).lines
    unbal.lines[0].debit = 11
    assert any("out of balance" in r for r in HITLGate.je_review_reasons(unbal))


def test_full_close_scripted(conn):
    co = CloseCoordinator(conn, gate_mode="scripted")
    rpt = co.run()
    assert rpt["completion_pct"] == 100 and rpt["pending_approvals"] == 0
    assert ledger.tb_is_balanced(conn, "2026-09")
    assert rpt["rejected_jes"] == ["UJE-104"]                         # unbalanced entry never posts
    ids = {j["je_id"] for j in rpt["posted_jes"]}
    assert {"ACR-001", "ACR-050", "AMT-080", "TIE-001", "UJE-102"} <= ids
    assert "ACR-002" not in ids                                       # legal accrual dedup'd vs UJE-103
    assert conn.execute("SELECT COUNT(*) FROM posted_je WHERE je_id='UJE-104'").fetchone()[0] == 0


def test_high_dollar_jes_were_escalated(conn):
    rpt = CloseCoordinator(conn).run()
    titles = " ".join(a["title"] for a in rpt["approvals"])
    for je in ("ACR-001", "ACR-050", "UJE-102"):
        assert je in titles
    auto = [j for j in rpt["posted_jes"] if j["approved_by"] == "auto-policy"]
    assert all(j["amount"] < 25_000 for j in auto)


def test_post_close_tie_out_and_flux_clean(conn):
    CloseCoordinator(conn).run()
    from tools import reconciliation, flux
    ties = {t.account: t for t in reconciliation.subledger_tieout(conn, "2026-09")}
    assert ties["4100"].ok                                            # revenue correction posted
    assert abs(ties["1100"].diff + 3_750) < 0.01                      # only the approved NSF reinstatement remains
    df = flux.run_flux(conn)
    assert "6700" not in set(df[df.flagged].account)                  # insurance amortization restored


def test_defer_mode_blocks_close(conn):
    co = CloseCoordinator(conn, gate_mode="defer")
    rpt = co.run()
    assert rpt["pending_approvals"] > 0 and rpt["completion_pct"] < 100
    assert any(s["status"] == "blocked" for s in rpt["steps"])


def test_events_stream(conn):
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    CloseCoordinator(conn, bus=bus).run()
    kinds = {e.kind for e in seen}
    assert {"reasoning", "resolved", "escalated", "approval", "step_start", "step_end"} <= kinds


def test_post_rejects_unbalanced(conn):
    import pytest
    je = JournalEntry.simple("T", "2026-09-30", "t", "6000", "2100", 10)
    je.lines[0].debit = 11
    with pytest.raises(ValueError):
        ledger.post_journal_entry(conn, je, "2026-09", "x")
