# Month-End Close Agent Demo

Multi-agent automation of a month-end financial close on synthetic data (period 2026-09 vs 2026-08).
Fully deterministic and offline: agents use rule-based reasoning, so no API key is needed.

```bash
pip install -r requirements.txt
python -m pytest -q                       # 26 tests
python run_demo.py                        # full close, scripted reviewer
python run_demo.py --approvals interactive   # you play the accountant
python run_demo.py --approvals defer         # escalations stay pending -> close blocked at ~69%
python run_demo.py --delay 0.15              # slow stream for presenting
```

## Layout
| Dir | Contents |
|---|---|
| `data/` | `generate_data.py` builds SQLite (`close_demo.db`) + CSVs: AP, AR, payroll, bank, unposted JEs, TB, prepaid register, commitments |
| `tools/` | `reconciliation.py` (bank match + sub-ledger tie-out), `accruals.py`, `flux.py`, `ledger.py` (GL posting) |
| `agents/` | `coordinator.py`, `reconciliation_agent.py`, `accrual_agent.py`, `variance_agent.py`, `hitl.py` (safety gate) |
| `ui/` | `dashboard.py` Rich CLI dashboard |
| `tests/` | pytest suite |

## Flow
Coordinator checklist: S1 validate TB -> S2 bank rec -> S3 sub-ledger tie-out -> S4 accruals/amortization ->
S5 review manual JEs -> S6 post + re-verify TB -> S7 flux & commentary -> S8 sign-off. Completion % updates per step.

## Built-in edge cases
- Unmatched bank fees/interest (auto-resolved with JE), NSF return and unidentified wire (escalated)
- Batch lockbox deposit, $0.35 short-pay within tolerance, outstanding check (timing difference)
- Missing accruals (hosting $31k, utilities, payroll cut-off $42k), missing insurance amortization
- Manual JE duplicating an agent accrual (suppressed); unbalanced manual JE (rejected)
- GL revenue $4,200 below AR sub-ledger (out-of-balance, escalated)
- Flux breaches: revenue, marketing (+107%), salaries; explained from sub-ledger drivers + posted JEs

## HITL policy (`config.py`)
JEs >= $25,000, any unbalanced JE, unmatched bank items >= $1,000, out-of-balance tie-outs,
unexplained flux >= $10,000 residual, and final sign-off all go to the gate.
