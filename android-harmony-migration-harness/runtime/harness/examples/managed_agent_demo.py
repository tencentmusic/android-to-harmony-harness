#!/usr/bin/env python3
"""Offline process-protocol demo; synthetic evidence never grants real delivery."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_adapter import FORMAT, TURN_FORMAT, EVENT_FORMAT
from agent_runner import drive
from demo import prepare, record_demo, approval_demo
from engine import Harness, runtime, read_json
from session import record


def worker(request_path, result_path):
    request = read_json(request_path)
    if request["next_action"]["action"] != "run_task":
        raise ValueError("Demo worker only executes the declared synthetic check tasks")
    experiment = Harness(request["run_dir"])
    with Path(request["events_path"]).open("x", encoding="utf-8") as stream:
        for sequence, kind in enumerate(("started", "completed"), 1):
            stream.write(json.dumps({"format": EVENT_FORMAT, **{key: request[key] for key in ("turn_id", "run_id", "session_id")},
                                     "sequence": sequence, "type": kind, "data": {}}) + "\n")
            stream.flush()
            if kind == "started":
                experiment.run(request["turn_id"], limit=1, task_ids=[request["next_action"]["task_id"]])
    runtime.write_json(result_path, {"format": TURN_FORMAT,
        **{key: request[key] for key in ("turn_id", "run_id", "session_id")}, "status": "completed"})


def demo(output):
    contract = prepare(Path(output).resolve())
    experiment = Harness.init(contract, Path(output) / "run")
    record_demo(experiment, "discover", "discover")
    approval_demo(experiment)
    record_demo(experiment, "implement", "implement")
    with experiment.locked():
        record(experiment.root, "session", {"action": "start", "session_id": "demo-request",
            "request": "Run two synthetic checks to demonstrate managed continuation",
            "scope": {"mode": "tasks", "task_ids": ["contracts", "build"]}, "evidence": [str(contract)]}, experiment)
    descriptor = Path(output) / "agent.json"
    runtime.write_json(descriptor, {"format": FORMAT, "id": "offline-demo", "mode": "managed",
        "command": ["{python}", str(Path(__file__).resolve()), "--request", "{request}", "--result", "{result}"],
        "supports_resume": False})
    return drive(experiment.root, descriptor, "demo-drive")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--request")
    parser.add_argument("--result")
    args = parser.parse_args()
    if args.request and args.result and not args.output:
        worker(args.request, args.result)
    elif args.output and not (args.request or args.result):
        result = demo(args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["can_end_turn"] else 1)
    else:
        parser.error("Choose --output or both --request and --result")
