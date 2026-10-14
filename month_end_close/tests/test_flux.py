from tools import flux


def test_flux_thresholds(conn):
    df = flux.run_flux(conn)
    flagged = set(df[df.flagged].account)
    assert {"4000", "4100", "6300", "6000", "5000"} <= flagged
    assert "6400" not in flagged                                   # rent flat


def test_pct_floor_ignores_immaterial(conn):
    df = flux.run_flux(conn, abs_threshold=10**9, pct_min_abs=10**9)
    assert not df.flagged.any()


def test_driver_analysis_identifies_customer(conn):
    d = flux.driver_analysis(conn, "4100", "2026-08", "2026-09")
    assert d[0]["driver"] == "Stark Industries" and d[0]["delta"] == 22_000
