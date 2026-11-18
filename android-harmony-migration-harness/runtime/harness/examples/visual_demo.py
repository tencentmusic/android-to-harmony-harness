"""Exercise the built-in visual adapter with synthetic captures and approvals."""
import argparse
import json
from pathlib import Path

from demo import Harness, approval_demo, prepare, record_demo, runtime


def run(root):
    from PIL import Image, ImageDraw

    root = Path(root).resolve()
    path = prepare(root)
    image = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((4, 4, 59, 20), fill="#12763f")
    draw.rectangle((8, 26, 53, 55), fill="#b6293d")
    image.save(root / "android/baseline.png")
    contract = json.loads(path.read_text(encoding="utf-8"))
    task = next(t for t in contract["tasks"] if t["kind"] == "visual")
    task.pop("command")
    task["visual_comparison"] = {aid: {"assertion_id": "observed", "capture_from": {
        "task_id": "device", "acceptance_id": aid.replace("-visual", "-device")}}
        for aid in task["acceptance_ids"]}
    runtime.write_json(path, contract)
    experiment = Harness.init(path, root / "run")
    record_demo(experiment, "discover", "discover")
    approval_demo(experiment)
    record_demo(experiment, "implement", "implement")
    experiment.run("pixel-checks")
    receipt = experiment.receipt("visual")
    if not receipt["accepted"]:
        raise ValueError("Built-in visual lifecycle did not pass")
    reports = [r for r in receipt["evidence"] if Path(r["path"]).name == "comparison.json"]
    if len(reports) != len(task["acceptance_ids"]):
        raise ValueError("Visual lifecycle omitted comparison reports")
    record_demo(experiment, "review", "review")
    approval_demo(experiment, "final", "final")
    result = experiment.verify()
    if result["status"] != "demo_only":
        raise ValueError("Synthetic visual lifecycle must remain demo_only")
    return {"status": "demo_only", "comparison_reports": len(reports), "quality_gate_granted": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), indent=2))
