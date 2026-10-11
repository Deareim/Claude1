"""Run the close and write one JSON doc per step (+ final summary) for the live artifact page."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from agents.coordinator import CloseCoordinator  # noqa: E402
from data import generate_data  # noqa: E402
from tools import ledger  # noqa: E402


def build(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    generate_data.build(config.DB_PATH)
    co = CloseCoordinator(ledger.connect(config.DB_PATH))
    pct_after: dict[str, float] = {}
    co.ctx.bus.subscribe(lambda e: e.kind == "step_end" and pct_after.update({e.step: e.data["pct"]}))
    rpt = co.run()
    ctx, files = co.ctx, []
    for i, s in enumerate(co.steps, 1):
        evs = [dict(agent=e.agent, kind=e.kind, message=e.message) for e in ctx.bus.events
               if e.step == s.step_id and e.kind not in ("step_start", "step_end")]
        doc = dict(id=s.step_id, order=i, name=s.name, owner=s.owner, status=s.status, pct=pct_after[s.step_id],
                   events=evs, approvals=sum(1 for r in ctx.gate.requests if r.step == s.step_id),
                   auto=sum(1 for r in ctx.resolved if r["step"] == s.step_id and r["category"] != "matched"))
        files.append(out_dir / f"{s.step_id}.json")
        files[-1].write_text(json.dumps(doc))
    final = dict(approvals=rpt["approvals"], resolved=ctx.resolved, posted_jes=rpt["posted_jes"],
                 rejected=[dict(je_id=j.je_id, description=j.description, amount=j.amount) for j in ctx.rejected],
                 commentary=rpt["commentary"], completion_pct=rpt["completion_pct"])
    files.append(out_dir / "final.json")
    files[-1].write_text(json.dumps(final, default=str))
    # blank step docs used to reset the page before a run
    for i, s in enumerate(co.steps, 1):
        (out_dir / f"reset_{s.step_id}.json").write_text(json.dumps(
            dict(id=s.step_id, order=i, name=s.name, owner=s.owner, status="pending", pct=0, events=[], approvals=0, auto=0)))
    return files


if __name__ == "__main__":
    print(*build(Path(sys.argv[1] if len(sys.argv) > 1 else config.OUTPUT_DIR / "live")), sep="\n")
