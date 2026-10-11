from datetime import date

from tools import accruals


def test_working_days():
    assert accruals.working_days_between(date(2026, 9, 25), date(2026, 9, 30)) == 3


def test_amortization_math():
    assert accruals.amortization_due(36_000, 12) == 3_000


def test_detects_expected_findings(conn):
    f = {(x.kind, x.subject): x for x in accruals.run_all(conn)}
    assert ("vendor_accrual", "Northwind Cloud") in f and f[("vendor_accrual", "Northwind Cloud")].amount == 31_000
    assert ("payroll_accrual", "Payroll") in f and abs(f[("payroll_accrual", "Payroll")].amount - 42_306.45) < 0.01
    assert ("amortization", "Annual insurance policy") in f
    assert ("amortization", "Annual dev-tools license") not in f      # already current
    assert ("vendor_accrual", "Lakeside Properties") not in f         # invoice exists
    assert f[("vendor_accrual", "Hartwell Legal LLP")].duplicate_of == "UJE-103"


def test_draft_jes_balanced(conn):
    for x in accruals.run_all(conn):
        assert x.je.is_balanced
