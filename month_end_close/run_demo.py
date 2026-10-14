#!/usr/bin/env python3
"""Run the month-end close demo.

  python run_demo.py                      # scripted reviewer, full close
  python run_demo.py --approvals interactive   # you are the accountant
  python run_demo.py --approvals defer         # escalations stay pending; see blocked close %
  python run_demo.py --delay 0.15              # slow it down for presenting
"""
import argparse
import json
import sys

import config
from agents.coordinator import CloseCoordinator
from data import generate_data
from tools import ledger
from ui.dashboard import Dashboard


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--approvals", choices=["scripted", "interactive", "defer"], default="scripted")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between log lines")
    ap.add_argument("--keep-data", action="store_true", help="reuse existing database instead of regenerating")
    a = ap.parse_args(argv)

    if not (a.keep_data and config.DB_PATH.exists()):
        generate_data.build(config.DB_PATH)
    conn = ledger.connect(config.DB_PATH)
    co = CloseCoordinator(conn, gate_mode=a.approvals)
    dash = Dashboard(co, delay=a.delay)
    dash.header()
    report = co.run()
    dash.summary(report)

    config.OUTPUT_DIR.mkdir(exist_ok=True)
    out = config.OUTPUT_DIR / "close_report.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    dash.con.print(f"Report written to {out}")
    return 0 if report["completion_pct"] >= 100 or a.approvals == "defer" else 1


if __name__ == "__main__":
    sys.exit(main())
