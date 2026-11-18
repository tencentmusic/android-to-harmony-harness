"""Offline Harness example; synthetic approvals never certify a real migration."""
import argparse
import base64
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Harness, runtime
from contract import FORMAT


def prepare(root, omit_popup=False):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    for name in ("android", "harmony"):
        shutil.copytree(Path(__file__).with_name("fixtures") / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "tools").mkdir()
    shutil.copyfile(Path(__file__).with_name("adapter.py"), root / "tools/adapter.py")
    for dimension in ("requirements", "technical", "ui"):
        (root / "tools" / (dimension + ".md")).write_text(
            "# Synthetic " + dimension + "\n\nDemo editor and photo source dialog. No real UI or approval.\n", encoding="utf-8")
    (root / "tools/project.md").write_text("# Demo project\nPython fixture only; no device or network.\n")
    (root / "android/baseline.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"))
    (root / "tools/settings.json").write_text(json.dumps({"omit_popup": omit_popup}))
    roots = {name: str(root / name) for name in ("android", "harmony", "tools")}
    def ref(path, locator, source="tools"):
        return {"root": source, "path": path, "locator": locator}
    profile = {"name": "offline-example", "modules": [{"id": "demo", "root": "harmony", "path": ".", "purpose": "Demo module"}],
               "knowledge": [ref("project.md", "project conventions")],
               "tools": [{"id": "python-suite", "purpose": "Run local synthetic checks", "argv": [sys.executable], "cwd": "tools"}]}
    runtime.write_json(root / "tools/project.json", profile)
    harmony = [{"root": "harmony", "include": ["*.py"]}]
    execution = harmony + [{"root": "tools", "include": ["adapter.py", "settings.json"]}]
    command = {"argv": [sys.executable, str(root / "tools/adapter.py"), "{request}", "{result}"], "cwd": "harmony", "timeout_seconds": 10}
    def task(tid, kind, dependencies, acceptance=()):
        result = {"id": tid, "kind": kind, "capability_ids": ["dynamics"], "depends_on": dependencies,
                  "acceptance_ids": list(acceptance), "inputs": [{"root": "android", "include": ["*.json"]}]
                  if kind == "discover" else execution, "tool_ids": [] if kind in {"discover", "implement", "review"} else ["python-suite"]}
        if kind not in {"discover", "implement", "review"}:
            result["command"] = command
        if kind == "visual":
            result["independent_points"] = True
        return result
    states = [{"id": sid, "capability_id": "dynamics", "kind": kind, "trigger": trigger,
               "expected": expected, "sources": [ref("reference.json", "synthetic source", "android")],
               "baselines": [ref("baseline.png", "synthetic state image", "android")]}
              for sid, kind, trigger, expected in [("editor", "page", "Open demo", "Synthetic editor visible"),
                                                   ("photo-source", "dialog", "Tap image", "Synthetic image dialog visible")]]
    acceptance = [{"id": "gifts", "capability_id": "dynamics", "kind": "contract", "required": True,
                   "behavior": "Independent reference gift quantities", "assertions": [
                       {"id": "gift-values", "operator": "equal", "expected": [1, 10, 100]}]}]
    for kind in ("device", "visual"):
        for state in states:
            acceptance.append({"id": state["id"] + "-" + kind, "capability_id": "dynamics", "kind": kind,
                "state_id": state["id"], "required": True, "behavior": "Synthetic " + state["expected"],
                "assertions": [{"id": "observed", "operator": "truthy", "expected": True}]})
    contract = {"format": FORMAT, "feature_id": "dynamics-demo", "demo": True, "policy": "faithful_migration",
                "roots": roots, "production_inputs": harmony,
                "project_profile": ref("project.json", "project profile"),
                "capabilities": [{"id": "dynamics", "behavior": "Demo editor and photo dialog", "has_ui": True,
                    "depends_on": [], "android_sources": [{"path": "reference.json", "locator": "gift_quantities"}]}],
                "states": states, "acceptance": acceptance,
                "review_scopes": [{"id": "dynamics", "state_ids": [s["id"] for s in states],
                    **{d: ref(d+".md", "synthetic "+d) for d in ("requirements", "technical", "ui")}}],
                "tasks": [task("discover", "discover", []), task("implement", "implement", ["discover"]),
                          task("contracts", "contract", ["implement"], ["gifts"]), task("build", "build", ["implement"]),
                          task("device", "device", ["build"], [a["id"] for a in acceptance if a["kind"] == "device"]),
                          task("visual", "visual", ["device"], [a["id"] for a in acceptance if a["kind"] == "visual"]),
                          task("review", "review", ["contracts", "device", "visual"])]}
    target = root / "contract.json"
    runtime.write_json(target, contract)
    return target


def record_demo(experiment, tid, operation_id):
    row = next(r for r in experiment.status()["tasks"] if r["id"] == tid)
    note = experiment.root / (tid + "-demo-note.txt")
    note.write_text("Synthetic checkpoint; no real business verification.\n")
    result = {"task_id": tid, "input_digest": row.get("input_digest"), "summary": "Offline example checkpoint", "evidence": [str(note)]}
    if tid == "review":
        result.update(reviewer="offline-demo", findings=[], acceptance_ids=list(experiment.acceptance))
    return experiment.record(operation_id, result)


def approval_demo(experiment, gate="joint", operation_id="demo-joint"):
    note = experiment.root / (gate + "-simulated-review.txt")
    note.write_text("SIMULATED REVIEW. This is not a human approval or business delivery.\n")
    result = {"gate": gate, "actor_type": "demo", "reviewer": "offline-example",
              "summary": "Simulate review protocol in demo mode only", "evidence": [str(note)]}
    if gate == "joint":
        result["reviews"] = [{"capability_id": r["capability_id"], "input_digest": r["input_digest"],
            "decisions": {d: "approved" for d in ("requirements", "technical", "ui")}} for r in experiment.status()["joint_review"]]
    else:
        result.update(input_digest=experiment.status()["final_review"].get("input_digest"), decision="approved")
    return experiment.approve(operation_id, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--omit-popup", action="store_true")
    args = parser.parse_args()
    path = prepare(args.output, args.omit_popup)
    experiment = Harness.init(path, Path(args.output) / "run")
    record_demo(experiment, "discover", "demo-discover")
    approval_demo(experiment)
    record_demo(experiment, "implement", "demo-implement")
    experiment.run("demo-batch")
    if "review" in experiment.status()["agent_tasks"]:
        record_demo(experiment, "review", "demo-review")
        approval_demo(experiment, "final", "demo-final")
    status = experiment.verify()
    print(json.dumps(status, indent=2))
    return 0 if status["status"] == "demo_only" else 1


if __name__ == "__main__":
    sys.exit(main())
