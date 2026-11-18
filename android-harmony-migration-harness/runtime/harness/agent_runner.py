"""Own Agent turns, enforce end checks, and retain uncertain attempts for reconciliation."""
import copy
import math
import os
from pathlib import Path
import sys
import time
import uuid

import foundation as base
from foundation import runtime
from engine import Harness
from agent_adapter import TURN_FORMAT, load_adapter, read_result, read_events
from session import current, load as load_session, record as record_session, recovery, end_check

FORMAT = "harness-agent-controller/1"
FILE = "agent-controller.json"


def load(directory, run_id=None):
    path = Path(directory) / FILE
    value = base.read_json(path) if path.exists() else {"format": FORMAT, "run_id": run_id, "operations": {}, "active": None}
    base.require(isinstance(value, dict) and value.get("format") == FORMAT
                 and isinstance(value.get("operations"), dict) and "active" in value, "Invalid Agent controller journal")
    base.require(run_id is None or value.get("run_id") == run_id, "Agent controller belongs to another run")
    for key, operation in value["operations"].items():
        base.require(isinstance(operation, dict) and operation.get("id") == key and base.identifier(key)
            and isinstance(operation.get("input"), dict) and isinstance(operation.get("receipts"), list)
            and all(type(operation.get(k)) is int and operation[k] >= 0 for k in ("turns", "no_progress", "reconnects"))
            and type(operation.get("started_at")) in (int, float) and math.isfinite(operation["started_at"])
            and (operation.get("result") is None or isinstance(operation["result"], dict)), "Invalid Agent controller operation")
    active = value["active"]
    base.require(active is None or isinstance(active, dict) and all(k in active for k in
                 ("turn_id", "operation_id", "request", "adapter")), "Invalid active Agent turn")
    if active:
        base.require(base.identifier(active["turn_id"]) and active["operation_id"] in value["operations"], "Unknown active Agent operation")
    previous = value.get("last_turn")
    base.require(previous is None or isinstance(previous, dict) and all(k in previous for k in
                 ("session_id", "adapter", "receipt", "resume_token", "status")), "Invalid previous Agent turn")
    return value


def inspect_control(directory):
    try:
        state_path = Path(directory) / base.STATE
        run_id = base.read_json(state_path)["run_id"] if state_path.is_file() else None
        value = load(directory, run_id)
        active = value["active"]
        observation = None
        if active:
            try:
                _, process, attempt = process_for(active)
                observation = {"process": process, "result_exists": (attempt / "result.json").is_file(),
                               "events_path": str(attempt / "events.jsonl"),
                               "replay_allowed": False, "next_action": "agent-reconcile"}
            except (OSError, ValueError, KeyError, TypeError) as error:
                observation = {"error": str(error), "replay_allowed": False, "next_action": "Inspect original request/process files"}
        operations = list(value["operations"].values())
        last = max(operations, key=lambda op: op.get("started_at", 0)) if operations else None
        return {"recorded": (Path(directory) / FILE).exists(), "pending_turn": active, "attempt_observation": observation,
                "journal_error": None, "enforcement": "managed_runner" if value["operations"] else "not_attached",
                "last_operation": {k: last.get(k) for k in ("id", "status", "turns", "no_progress", "reconnects")} if last else None}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"recorded": True, "pending_turn": None, "journal_error": str(error), "enforcement": "invalid_journal"}


def save(root, document):
    runtime.write_json(Path(root) / FILE, document)


def observe(root):
    experiment = Harness(root)
    with experiment.locked():
        status = experiment.status()
        state = recovery(experiment, status)
        session = current(load_session(root))
        for ref in session["evidence"]:
            base.check_record(ref)
        return experiment, status, state, session


def fingerprint(status, state):
    # Journal timestamps and Agent self-reported progress cannot reset the stall budget.
    return runtime.digest({"tasks": [{k: r.get(k) for k in ("id", "status", "input_digest", "reason")} for r in status["tasks"]],
        "coverage": status["coverage"], "joint_review": status["joint_review"], "final_review": status["final_review"],
        "continuation": state["continuation"], "holds": state["external_holds"]})


def check_end(root, session_id):
    experiment = Harness(root)
    with experiment.locked():
        state = recovery(experiment)
        result = end_check(experiment, "agent-end-" + uuid.uuid4().hex,
                           {"session_id": session_id, "reason": "request_complete"})
        if not result["allowed"] and not any(a["owner"] == "agent" for a in state["pending_actions"]):
            reason = "waiting_external" if state["external_holds"] else "waiting_human"
            result = end_check(experiment, "agent-end-" + uuid.uuid4().hex,
                               {"session_id": session_id, "reason": reason})
        return result


def process_for(active, root=None):
    base.check_record(active["request"])
    request = base.read_json(active["request"]["path"])
    directory = Path(active["request"]["path"]).parent
    base.require(request.get("format") == TURN_FORMAT and request.get("turn_id") == active["turn_id"], "Agent request belongs to another turn")
    if root is not None:
        base.require(directory == Path(root) / "agent-turns" / active["turn_id"] and request.get("run_dir") == str(root),
                     "Agent request belongs to another run directory")
    process = base.read_json(directory / "process.json")
    base.require(process.get("argv") == request["transport"]["argv"] and process.get("cwd") == request["transport"]["cwd"]
                 and process.get("log_path") == str(directory / "process.log"), "Agent process belongs to another attempt")
    return request, process, directory


def confirm_stopped(experiment, process):
    if process.get("status") == "interrupted" and process.get("process_started") is False and process.get("pid") is None:
        return {"process_never_started": True, "checked_at": time.time()}
    if os.name == "posix":
        return experiment.confirm_process_stopped(process)
    base.require(process.get("status") in {"exited", "timed_out", "idle_timeout"} and "finished_at" in process,
                 "Agent process has no settled terminal record; inspect original process first")
    return {"terminal_record": True, "checked_at": time.time()}


def consume(experiment, document, active, persist=True):
    request, process, directory = process_for(active, experiment.root)
    base.require(request["run_id"] == experiment.state["run_id"], "Agent request belongs to another run")
    stopped = confirm_stopped(experiment, process)
    result = read_result(directory / "result.json", request)
    events = read_events(directory / "events.jsonl", request)
    success = process.get("status") == "exited" and process.get("exit_code") == 0
    base.require(success or result["status"] == "interrupted", "Only a successful result or explicit interrupted envelope can be consumed")
    receipt = {"turn_id": active["turn_id"], "request": active["request"], "adapter": active["adapter"],
        "process": base.file_record(directory / "process.json"), "result": base.file_record(directory / "result.json"),
        "events": base.file_record(directory / "events.jsonl") if events else None,
        "event_count": len(events), "stopped": stopped, "status": result["status"], "recorded_at": time.time()}
    target = directory / "receipt.json"
    if target.exists():
        previous = base.read_json(target)
        base.require(all(previous[k] == receipt[k] for k in ("turn_id", "request", "adapter", "process", "result", "events", "status")),
                     "Agent receipt changed before reconciliation")
    else:
        runtime.write_json(target, receipt)
    operation = document["operations"][active["operation_id"]]
    ref = base.file_record(target)
    if ref not in operation["receipts"]:
        operation["receipts"].append(ref)
    document["last_turn"] = {"session_id": request["session_id"], "adapter": active["adapter"],
        "receipt": ref, "resume_token": result.get("resume_token"), "status": result["status"]}
    document["active"] = None
    if persist:
        save(experiment.root, document)
    return result


def finish(root, document, operation, status, **details):
    result = {"status": status, "operation_id": operation["id"], "turns": operation["turns"],
        "can_end_turn": False, "enforcement": "managed_runner", "quality_gate_granted": False,
        "background_worker": False, **details}
    operation.update(status=status, result=result, updated_at=time.time())
    save(root, document)
    return result


def interrupt_session(root, session_id, summary):
    experiment = Harness(root)
    with experiment.locked():
        session = current(load_session(root))
        if session["id"] == session_id and session["state"] == "working":
            record_session(root, "agent-interrupt-" + uuid.uuid4().hex,
                {"action": "interrupt", "session_id": session_id, "summary": summary}, experiment)


def drive(root, adapter_path, operation_id, max_turns=20, max_no_progress=3, max_reconnects=2, max_seconds=1800, resume=False):
    root = Path(root).resolve(strict=True)
    base.require(base.identifier(operation_id), "Invalid Agent operation ID")
    base.require(all(type(n) is int and n > 0 for n in (max_turns, max_no_progress, max_seconds))
                 and type(max_reconnects) is int and max_reconnects >= 0, "Invalid Agent execution budgets")
    adapter = load_adapter(adapter_path)
    base.require(adapter["mode"] == "managed", "agent-drive requires a managed process adapter")
    adapter_ref = base.file_record(adapter["descriptor"])
    with runtime.file_lock(root / "agent-controller.lock", timeout=0) as acquired:
        base.require(acquired, "Agent controller is busy; inspect agent-status")
        experiment, _, _, session = observe(root)
        document = load(root, experiment.state["run_id"])
        base.require(not document["active"], "Unresolved Agent attempt; use agent-reconcile before another invocation")
        inputs = {"adapter": adapter_ref, "session_id": session["id"], "max_turns": max_turns,
                  "max_no_progress": max_no_progress, "max_reconnects": max_reconnects, "max_seconds": max_seconds}
        operation = document["operations"].get(operation_id)
        if operation:
            base.require(operation["input"] == inputs, "Agent operation ID reused with different inputs")
            if operation.get("result"):
                saved = copy.deepcopy(operation["result"])
                if saved.get("can_end_turn"):
                    receipt = saved["end_check"]
                    with experiment.locked():
                        checked = end_check(experiment, Path(receipt["receipt_path"]).stem,
                                            {"session_id": session["id"], "reason": receipt["reason"]})
                    saved.update(can_end_turn=checked["allowed"], current=checked["allowed"])
                    if not checked["allowed"]:
                        saved.update(status="stale_operation", next_action="Use a new operation ID and --resume with current evidence")
                return {**saved, "replayed": True}
            base.require(resume, "An interrupted controller requires explicit --resume")
        else:
            operation = {"id": operation_id, "input": inputs, "status": "working", "started_at": time.time(),
                         "turns": 0, "no_progress": 0, "reconnects": 0, "receipts": []}
            document["operations"][operation_id] = operation
        base.require(session["state"] == "working" or resume, "Use --resume to continue a waiting, ended or interrupted session")
        if session["state"] != "working":
            with experiment.locked():
                record_session(root, "agent-resume-" + uuid.uuid4().hex,
                    {"action": "resume", "session_id": session["id"], "summary": "Explicit managed controller continuation"}, experiment)
        save(root, document)
        while True:
            base.check_record(adapter_ref)
            experiment, status, state, session = observe(root)
            base.require(session["id"] == inputs["session_id"], "Active user request changed; explicitly start a new controller operation")
            if session["state"] != "working":
                return finish(root, document, operation, "interrupted", next_action="Inspect the session transition before explicitly resuming")
            checked = check_end(root, session["id"])
            if checked["allowed"]:
                return finish(root, document, operation, checked["reason"], can_end_turn=True, end_check=checked,
                              migration_complete=checked["migration_complete"])
            remaining = max_seconds - (time.time() - operation["started_at"])
            exhausted = ("time_budget" if remaining <= 0 else "turn_budget" if operation["turns"] >= max_turns else
                         "no_progress" if operation["no_progress"] >= max_no_progress else None)
            if exhausted:
                return finish(root, document, operation, exhausted, next_action=checked["next_action"])
            actions = state["pending_actions"]
            if not actions or state["journal_error"]:
                return finish(root, document, operation, "needs_diagnosis", next_action=state["next_action"])
            previous = document.get("last_turn")
            if previous and (previous["session_id"] != session["id"] or previous["adapter"] != adapter_ref):
                previous = None
            if previous:
                base.check_record(previous["receipt"])
                receipt = base.read_json(previous["receipt"]["path"])
                for key in ("request", "adapter", "process", "result"):
                    base.check_record(receipt[key])
                if receipt.get("events"):
                    base.check_record(receipt["events"])
                base.require(base.read_json(receipt["result"]["path"]).get("resume_token") == previous["resume_token"],
                             "Saved Agent resume token changed")
            token = previous.get("resume_token") if previous and adapter["supports_resume"] else None
            turn_id = "turn-" + uuid.uuid4().hex
            directory = root / "agent-turns" / turn_id
            directory.mkdir(parents=True)
            argv = [{"{request}": str(directory / "request.json"), "{result}": str(directory / "result.json"),
                     "{events}": str(directory / "events.jsonl")}.get(a, a)
                    for a in adapter["command"]]
            entrypoint = Path(__file__).resolve().parents[2] / "scripts/harness.py"
            if not entrypoint.is_file():
                entrypoint = Path(__file__).with_name("migration.py")
            request = {"format": TURN_FORMAT, "turn_id": turn_id, "run_id": experiment.state["run_id"],
                "session_id": session["id"], "user_request": session["request"], "scope": session["scope"],
                "action": "resume" if token else "execute", "resume_token": token,
                "run_dir": str(root), "harness_command": [sys.executable, str(entrypoint)],
                "events_path": str(directory / "events.jsonl"),
                "next_action": actions[0], "pending_actions": actions, "recovery": state,
                "previous_turn": previous, "replay_saved_commands": False, "agent_completion_is_advisory": True,
                "transport": {"argv": argv, "cwd": adapter["cwd"]}}
            runtime.write_json(directory / "request.json", request)
            document["active"] = {"turn_id": turn_id, "operation_id": operation_id,
                "request": base.file_record(directory / "request.json"), "adapter": adapter_ref}
            operation["turns"] += 1
            operation["status"] = "working"
            before = fingerprint(status, state)
            save(root, document)
            try:
                runtime.run_process(argv, cwd=adapter["cwd"], log_path=directory / "process.log",
                    progress_path=directory / "process.json", timeout_seconds=min(adapter["timeout_seconds"], remaining),
                    idle_timeout_seconds=min(adapter["idle_timeout_seconds"], remaining))
                result = consume(experiment, document, document["active"])
            except (OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as error:
                interrupt_session(root, session["id"], "Agent transport/result requires reconciliation: " + str(error))
                return finish(root, document, operation, "in_doubt", error=str(error),
                              next_action="Inspect original process and result, then agent-reconcile; no automatic replay")
            _, after_status, after_state, _ = observe(root)
            operation["no_progress"] = 0 if fingerprint(after_status, after_state) != before else operation["no_progress"] + 1
            if result["status"] == "interrupted":
                checked = check_end(root, session["id"])
                if checked["allowed"]:
                    return finish(root, document, operation, checked["reason"], can_end_turn=True, end_check=checked,
                                  migration_complete=checked["migration_complete"])
                safe_resume = adapter["supports_resume"] and result.get("resume_token") and not after_state["active_attempts"]
                if not safe_resume or operation["reconnects"] >= max_reconnects:
                    interrupt_session(root, session["id"], "Agent connection interrupted; automatic resume unavailable or exhausted")
                    return finish(root, document, operation, "interrupted", next_action="Inspect current evidence, then explicitly resume")
                operation["reconnects"] += 1
                interrupt_session(root, session["id"], "Settled interrupted Agent turn; resuming the same conversation")
                with experiment.locked():
                    record_session(root, "agent-reconnect-" + uuid.uuid4().hex,
                        {"action": "resume", "session_id": session["id"], "summary": "Bounded reconnect using the validated resume token"}, experiment)
            save(root, document)


def reconcile(root, operation_id, value):
    root = Path(root).resolve(strict=True)
    base.require(base.identifier(operation_id) and isinstance(value, dict), "Invalid Agent reconciliation input")
    base.require(value.get("action") in {"accept", "retry"} and base.text(value.get("reason")), "Reconciliation needs action and diagnostic reason")
    refs = value.get("evidence")
    base.require(isinstance(refs, list) and refs, "Reconciliation needs diagnostic evidence")
    refs = [base.file_record(p) for p in refs]
    with runtime.file_lock(root / "agent-controller.lock", timeout=0) as acquired:
        base.require(acquired, "Agent controller is busy")
        experiment = Harness(root)
        document = load(root, experiment.state["run_id"])
        existing = document.setdefault("reconciliations", {}).get(operation_id)
        if existing:
            base.require(existing["input"] == value, "Reconciliation operation ID reused with different input")
            for ref in existing["evidence"]:
                base.check_record(ref)
            return {**existing["result"], "replayed": True}
        active = document["active"]
        base.require(active and value.get("turn_id") == active["turn_id"], "Reconcile the current unresolved Agent turn")
        request, process, directory = process_for(active, root)
        base.require(request["run_id"] == experiment.state["run_id"], "Agent request belongs to another run")
        stopped = confirm_stopped(experiment, process)
        if value["action"] == "accept":
            consume(experiment, document, active, persist=False)
        else:
            # This explicit diagnostic decision permits a fresh call, never an automatic replay.
            document["active"] = None
            document.pop("last_turn", None)
        op = document["operations"][active["operation_id"]]
        op.update(status="reconciled", result=None)
        result = {"status": "reconciled", "turn_id": active["turn_id"], "action": value["action"],
                  "process_observation": stopped, "replay_performed": False, "quality_gate_granted": False,
                  "next_action": "Inspect task attempts and agent-drive --resume; budgets on the original operation remain in force"}
        document["reconciliations"][operation_id] = {"input": value, "evidence": refs, "result": result,
            "request": active["request"], "process": base.file_record(directory / "process.json")}
        save(root, document)
        return result
