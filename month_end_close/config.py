"""Central close policy: periods, tolerances and HITL thresholds."""
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "close_demo.db"
CSV_DIR = ROOT / "data" / "csv"
OUTPUT_DIR = ROOT / "output"

PERIOD = "2026-09"        # M0, the month being closed
PRIOR_PERIOD = "2026-08"  # M-1
PERIOD_END = "2026-09-30"

# Bank reconciliation
BANK_AMOUNT_TOL = 1.00      # max $ difference for a tolerance match
BANK_DATE_WINDOW = 5        # max days between book and bank dates
OUTSTANDING_WINDOW = 5      # book items this close to month-end are timing differences
AUTO_FEE_MAX = 250.00       # bank fees/interest below this are auto-resolved

# Sub-ledger to GL tie-out
SUBLEDGER_TOL = 1.00

# Flux analysis: flag if |var| >= FLUX_ABS, or pct >= FLUX_PCT and |var| >= FLUX_PCT_MIN_ABS
FLUX_PCT = 0.10
FLUX_ABS = 10_000.00
FLUX_PCT_MIN_ABS = 1_000.00
FLUX_UNEXPLAINED_ESCALATE = 10_000.00

# Human-in-the-loop safety gate
HITL_JE_THRESHOLD = 25_000.00     # JEs at/above this need accountant approval
HITL_UNMATCHED_THRESHOLD = 1_000.00
