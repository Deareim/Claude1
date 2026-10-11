"""Run the close and export a self-contained HTML report (agent log, approvals, JEs, flux)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from agents.coordinator import CloseCoordinator  # noqa: E402
from data import generate_data  # noqa: E402
from tools import ledger  # noqa: E402

TEMPLATE = Path(__file__).with_name("report_template.html")


def build(out: Path = config.OUTPUT_DIR / "close_report.html", mode: str = "scripted") -> Path:
    generate_data.build(config.DB_PATH)
    co = CloseCoordinator(ledger.connect(config.DB_PATH), gate_mode=mode)
    rpt = co.run()
    ctx = co.ctx
    rpt["events"] = [dict(agent=e.agent, kind=e.kind, message=e.message, step=e.step, amount=e.data.get("amount"))
                     for e in ctx.bus.events if e.kind not in ("step_start",)]
    rpt["resolved"] = ctx.resolved
    rpt["rejected"] = [dict(je_id=j.je_id, description=j.description, amount=j.amount) for j in ctx.rejected]
    out.parent.mkdir(exist_ok=True)
    out.write_text(TEMPLATE.read_text().replace("__DATA__", json.dumps(rpt, default=str)))
    return out


if __name__ == "__main__":
    print(build())
