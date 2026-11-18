"""Host lifecycle receipts, separate from migration quality evidence."""
import copy
import math
import os
import time
from pathlib import Path

import foundation as base
from foundation import runtime
from continuation import advice

FORMAT = "migration-agent-session/1"
FILE = "agent-session.json"
PHASES = {"discovery", "review_preparation", "implementation", "diagnosis", "setup", "build",
          "install", "navigate", "wait", "assert", "capture", "compare", "cleanup"}


def load(directory):
    path = Path(directory) / FILE
    if not path.exists():
        return {"format": FORMAT, "sessions": {}, "holds": {}, "operations": {}, "events": []}
    value = base.read_json(path)
    base.require(isinstance(value, dict) and value.get("format") == FORMAT, "Invalid agent session journal")
    base.require(all(isinstance(value.get(key), dict) for key in ("sessions", "holds", "operations"))
                 and isinstance(value.get("events"), list), "Invalid agent session collections")
    base.require(all(isinstance(row, dict) for key in ("sessions", "holds", "operations") for row in value[key].values()),
                 "Invalid agent session records")
    base.require(not value["sessions"] or value.get("current_session_id") in value["sessions"], "Missing current session")
    for row in value["sessions"].values():
        base.require(base.identifier(row.get("id")) and row.get("state") in {
            "working", "interrupted", "ended", "paused", "waiting_human", "waiting_external"}
            and row.get("phase") in PHASES and isinstance(row.get("evidence"), list)
            and type(row.get("resume_generation", 0)) is int and row.get("resume_generation", 0) >= 0,
            "Invalid agent session state")
        scope(row.get("scope"))
    for event in value["events"]:
        base.require(isinstance(event, dict) and base.text(event.get("kind")) and type(event.get("at")) in (int, float)
                     and math.isfinite(event["at"]), "Invalid agent session event")
        if "state" in event:
            base.require(event["state"] in {"working", "interrupted", "ended", "paused", "waiting_human", "waiting_external"},
                         "Invalid agent session transition")
        if event["kind"] == "end_check":
            base.require(type(event.get("allowed")) is bool, "Invalid end-check event")
    return value


def evidence(value, required=False):
    paths = value.get("evidence", [])
    base.require(isinstance(paths, list) and (paths or not required), "Lifecycle action needs evidence paths")
    return [base.file_record(p) for p in paths]


def scope(value, experiment=None):
    base.require(isinstance(value, dict) and value.get("mode") in {"full", "review", "tasks"}, "Invalid request scope")
    result = copy.deepcopy(value)
    key = {"review": "capability_ids", "tasks": "task_ids"}.get(value["mode"])
    allowed = {"mode", key} if key else {"mode"}
    base.require(set(value) <= allowed, "Unknown request scope field")
    if key:
        base.require(base.ids(value.get(key)) and value[key], "Request scope needs explicit IDs")
        if experiment:
            known = experiment.caps if key == "capability_ids" else experiment.tasks
            base.require(set(value[key]) <= set(known), "Unknown request scope ID")
    return result


def request_tasks(experiment, request_scope):
    if request_scope["mode"] == "full":
        return set(experiment.tasks)
    if request_scope["mode"] == "review":
        caps = set(request_scope["capability_ids"])
        selected = {tid for tid, task in experiment.tasks.items() if task["kind"] == "discover"
                    and caps.intersection(task["capability_ids"])}
    else:
        selected = set(request_scope["task_ids"])
    todo = list(selected)
    while todo:
        for dependency in experiment.tasks[todo.pop()].get("depends_on", []):
            if dependency not in selected:
                selected.add(dependency)
                todo.append(dependency)
    return selected


def request_actions(experiment, request_scope, actions):
    if request_scope["mode"] == "full":
        return actions
    selected = request_tasks(experiment, request_scope)
    caps = {cid for tid in selected for cid in experiment.tasks[tid]["capability_ids"]}
    return [a for a in actions if a.get("task_id") in selected or a.get("capability_id") in caps
            or a.get("action") in {"repair_session_journal", "inspect_agent_turn"}]


def append(document, kind, **details):
    event = {"kind": kind, "at": time.time(), **details}
    document["events"].append(event)
    sid = document.get("current_session_id")
    if sid:
        document["sessions"][sid]["last_event_at"] = event["at"]
    return event


def current(document):
    sid = document.get("current_session_id")
    base.require(sid in document["sessions"], "Start an agent session first")
    return document["sessions"][sid]


def binding(experiment, rows):
    return {"run_id": experiment.state["run_id"], "contract": experiment.state["contract"],
            "tasks": [{k: row.get(k) for k in ("id", "status", "input_digest", "reason")} for row in rows]}


def active_holds(experiment, document, status):
    rows = {r["id"]: r for r in status["tasks"]}
    accepted, invalid = [], []
    for hold in document["holds"].values():
        if hold.get("resolved_at"):
            continue
        try:
            base.require(hold["expires_at"] > time.time(), "External hold expired")
            for item in hold["evidence"]:
                base.check_record(item)
            actual = binding(experiment, [rows[tid] for tid in hold["task_ids"]])
            base.require(actual == hold["binding"], "External hold inputs/status changed")
            accepted.append(hold)
        except (OSError, ValueError, KeyError, TypeError) as error:
            invalid.append({"id": hold.get("id", "unknown"), "reason": str(error)})
    return accepted, invalid


def process_state(saved):
    running = saved.get("running")
    if not running:
        return None
    result = {"directory": running["directory"], "request": running["request"],
              "action": "inspect_before_retry", "replay_allowed": False}
    try:
        base.check_record(running["request"])
        directory = Path(running["directory"])
        process = base.read_json(directory / "process.json")
        result["process"] = process
        if process.get("status") == "exited" and (directory / "result.json").is_file():
            result["action"] = "reconcile_completed_result"
        elif process.get("status") == "running":
            pid = process.get("pid")
            alive = None
            if os.name == "posix" and type(pid) is int and pid > 1:
                try:
                    os.kill(pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    pass
            result.update(pid_observed_alive=alive, action="observe_process" if alive else "inspect_before_reconcile")
    except (OSError, ValueError, KeyError, TypeError) as error:
        result["reason"] = str(error)
    return result


def recovery(experiment, status=None, continuation=None):
    from agent_runner import inspect_control
    status = experiment.status() if status is None else status
    journal_error = None
    try:
        document = load(experiment.root)
        if document.get("current_session_id"):
            scope(document["sessions"][document["current_session_id"]]["scope"], experiment)
    except (OSError, ValueError, KeyError, TypeError) as error:
        journal_error = str(error)
        document = {"sessions": {}, "holds": {}, "events": []}
    continuation = advice(experiment, status) if continuation is None else continuation
    holds, invalid = active_holds(experiment, document, status)
    sid = document.get("current_session_id")
    session = document["sessions"].get(sid)
    selected = request_tasks(experiment, session["scope"]) if session else set(experiment.tasks)
    holds = [h for h in holds if selected.intersection(h["task_ids"])]
    held = {tid for h in holds for tid in h["task_ids"]}
    actions = [a for a in continuation["pending_actions"] if a.get("task_id") not in held]
    if session:
        actions = request_actions(experiment, session["scope"], actions)
    control = inspect_control(experiment.root)
    if control["pending_turn"] or control["journal_error"]:
        actions.insert(0, {"owner": "agent", "action": "inspect_agent_turn", "command": "agent-status",
                          "replay_saved_commands": False, "reason": control["journal_error"] or "Reconcile the original Agent process and result"})
    if journal_error:
        actions.insert(0, {"owner": "agent", "action": "repair_session_journal", "reason": journal_error})
    pending = [{"task_id": tid, **process_state(saved)} for tid, saved in experiment.state["tasks"].items()
               if saved.get("running")]
    state = ("interrupted" if pending or control["pending_turn"] or control["journal_error"] or session and session["state"] == "interrupted" else
             "paused" if session and session["state"] == "paused" else
             "working" if any(a["owner"] == "agent" for a in actions) else
             "waiting_external" if holds else "waiting_human" if actions else "complete")
    if not control["pending_turn"] and not control["journal_error"] and session and session["state"] == "ended" and session.get("end_receipt"):
        try:
            base.check_record(session["end_receipt"])
            receipt = base.read_json(session["end_receipt"]["path"])
            for ref in session["evidence"] + receipt["evidence"]:
                base.check_record(ref)
            if (receipt["snapshot"].get("session_generation") == session.get("resume_generation", 0)
                    and receipt["snapshot"]["binding"] == binding(experiment, status["tasks"])
                    and receipt["snapshot"]["continuation"] == continuation
                    and receipt["snapshot"]["holds"] == holds):
                state = "request_complete"
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return {"format": FORMAT, "state": state, "session": session, "active_attempts": pending,
            "journal_error": journal_error,
            "checkpoint_current": (session.get("checkpoint", {}).get("event_sequence") == len(experiment.state["events"])) if session else None,
            "last_progress_at": session.get("last_progress_at") if session else None,
            "next_action": actions[0] if actions else None, "pending_actions": actions,
            "external_holds": holds, "invalid_holds": invalid, "continuation": continuation,
            "agent_control": control,
            "quality_gate_granted": False, "background_worker": False,
            "note": "Read-only recovery. PID and heartbeat observations never authorize replay; reconcile validates original evidence."}


def metrics(directory):
    try:
        document = load(directory)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"recorded": False, "status": "invalid_journal", "reason": str(error), "unobserved_seconds": None}
    totals = {}
    events = document["events"]
    transitions = [event for event in events if "state" in event]
    for event, end in zip(transitions, [e["at"] for e in transitions[1:]] + [time.time()]):
        if event["state"] in {"ended", "attached"}:
            continue
        key = event.get("phase", "unknown") if event["state"] == "working" else event["state"]
        totals[key] = totals.get(key, 0) + max(0, end - event["at"])
    return {"recorded": bool(events), "seconds_by_state_or_phase": {k: round(v, 3) for k, v in totals.items()},
            "interruptions": sum(e["kind"] == "interrupt" for e in events),
            "denied_end_checks": sum(e["kind"] == "end_check" and not e["allowed"] for e in events),
            "unobserved_seconds": None, "note": "Session wall spans include tool work; do not add them to executor seconds."}


def record(directory, operation_id, value, experiment=None):
    directory = Path(directory)
    base.require(base.identifier(operation_id) and isinstance(value, dict), "Session action needs operation ID and object")
    directory.mkdir(parents=True, exist_ok=True)
    document = load(directory)
    base.require(not document.get("attached_to") or experiment, "Use the attached run journal")
    if operation_id in document["operations"]:
        saved = document["operations"][operation_id]
        base.require(saved["input"] == value, "Lifecycle operation ID reused with different input")
        return {**saved["result"], "replayed": True}
    action = value.get("action")
    if action == "attach":
        base.require(experiment, "Attach requires a migration run")
        source = Path(value["journal"]).resolve(strict=True)
        base.require(source != directory.resolve(), "Journal is already attached here")
        base.require(not document["sessions"],
                     "Attach requires a fresh run session journal")
        with runtime.file_lock(source / "agent-session.lock", timeout=0) as acquired:
            base.require(acquired, "Pre-run session journal is busy")
            document = load(source)
            base.require(document["sessions"] and (not document.get("run_id") and not document.get("attached_to") or
                         document.get("run_id") == experiment.state["run_id"] and document.get("attached_to") == str(directory.resolve())),
                         "Journal already bound")
            for row in document["sessions"].values():
                scope(row["scope"], experiment)
            saved = document["operations"].get(operation_id)
            if document.get("attached_to"):
                base.require(saved and saved["input"] == value and "result" in saved,
                             "Resume the reserved attach with its original operation ID and input")
                result = copy.deepcopy(saved["result"])
                document.pop("attached_to")
            else:
                base.require(not saved, "Attach needs an unused lifecycle operation ID")
                document["run_id"] = experiment.state["run_id"]
                document["source_journal"] = str(source)
                append(document, "attach", run_id=document["run_id"])
                result = {"status": "recorded", "session": current(document), "quality_gate_granted": False}
                document["operations"][operation_id] = {"input": value, "result": copy.deepcopy(result)}
            # Both writes include the operation receipt, so an interrupted attach
            # can finish without replacing any subsequently recorded run events.
            runtime.write_json(source / FILE, {**document, "attached_to": str(directory.resolve())})
            runtime.write_json(directory / FILE, document)
            return result
    elif action == "start":
        sid = value.get("session_id")
        base.require(base.identifier(sid) and sid not in document["sessions"], "Use a new session ID")
        base.require(base.text(value.get("request")), "Preserve the current user request")
        request_scope = scope(value.get("scope"), experiment)
        refs = evidence(value, request_scope["mode"] != "full")
        previous = document["sessions"].get(document.get("current_session_id"))
        base.require(not previous or previous["state"] in {"ended", "paused", "interrupted", "waiting_human", "waiting_external"},
                     "Resume or end the active session first")
        now = time.time()
        document["sessions"][sid] = {"id": sid, "scope": request_scope, "request": value["request"],
            "evidence": refs, "state": "working", "phase": "discovery", "started_at": now, "last_progress_at": now,
            "resume_generation": 0}
        document["current_session_id"] = sid
        append(document, "start", state="working", phase="discovery", session_id=sid)
    else:
        session = current(document)
        base.require(value.get("session_id") == session["id"], "Session ID does not match the active request")
        if action in {"resume", "interrupt", "phase"}:
            base.require(base.text(value.get("summary")), "Lifecycle event needs a summary")
            phase = value.get("phase", session["phase"])
            base.require(phase in PHASES, "Unknown session phase")
            base.require(action == "resume" or session["state"] == "working", "Resume the session before recording work")
            state = "interrupted" if action == "interrupt" else "working"
            if action == "resume":
                session["resume_generation"] = session.get("resume_generation", 0) + 1
            session.update(state=state, phase=phase)
            append(document, action, state=state, phase=phase, summary=value["summary"], evidence=evidence(value))
        elif action == "hold":
            base.require(experiment and base.identifier(value.get("id")), "Hold requires a run and stable hold ID")
            base.require(value["id"] not in document["holds"], "Use a new hold ID")
            tids = value.get("task_ids")
            base.require(base.ids(tids) and tids and set(tids) <= set(experiment.tasks), "Hold needs known root tasks")
            base.require(base.text(value.get("reason")) and base.text(value.get("resume_condition")), "Hold needs reason and resume condition")
            expires = value.get("expires_at")
            base.require(type(expires) in (int, float) and math.isfinite(expires) and time.time() < expires <= time.time() + 604800,
                         "Hold needs a future expiry within seven days")
            rows = {r["id"]: r for r in experiment.status()["tasks"]}
            base.require(all(rows[tid]["status"] not in {"passed", "in_doubt", "blocked", "awaiting_human"} for tid in tids),
                         "Hold root tasks only; reconcile interrupted attempts and preserve human gates")
            document["holds"][value["id"]] = {"id": value["id"], "task_ids": tids, "reason": value["reason"],
                "resume_condition": value["resume_condition"], "expires_at": expires, "evidence": evidence(value, True),
                "binding": binding(experiment, [rows[tid] for tid in tids])}
            append(document, "hold", hold_id=value["id"])
        elif action == "release":
            hold = document["holds"].get(value.get("id"))
            base.require(hold and not hold.get("resolved_at") and base.text(value.get("summary")), "Release needs an active hold and reason")
            hold.update(resolved_at=time.time(), resolution=value["summary"], resolution_evidence=evidence(value, True))
            append(document, "release", hold_id=hold["id"])
        else:
            raise ValueError("Unknown session action")
    if experiment:
        base.require(document.get("run_id", experiment.state["run_id"]) == experiment.state["run_id"], "Journal belongs to another run")
        document["run_id"] = experiment.state["run_id"]
    result = {"status": "recorded", "session": current(document), "quality_gate_granted": False}
    document["operations"][operation_id] = {"input": value, "result": copy.deepcopy(result)}
    runtime.write_json(directory / FILE, document)
    return result


def end_check(experiment, operation_id, value):
    from agent_runner import inspect_control
    base.require(isinstance(value, dict), "End check needs an input object")
    document = load(experiment.root)
    session = current(document)
    base.require(base.identifier(operation_id) and value.get("session_id") == session["id"], "End check needs current session ID")
    refs = evidence(value)
    for item in session["evidence"]:
        base.check_record(item)
    status = experiment.status()
    continuation = advice(experiment, status)
    holds, invalid = active_holds(experiment, document, status)
    request_scope = scope(session["scope"], experiment)
    selected = request_tasks(experiment, request_scope)
    holds = [h for h in holds if selected.intersection(h["task_ids"])]
    held = {tid for h in holds for tid in h["task_ids"]}
    actions = request_actions(experiment, request_scope, continuation["pending_actions"])
    agents = [a for a in actions if a["owner"] == "agent" and a.get("task_id") not in held]
    reason = value.get("reason")
    allowed = False
    if reason == "user_paused":
        base.require(value.get("actor_type") == "human" and base.text(value.get("user_instruction")), "Pause requires the actual human instruction")
        refs = evidence(value, True)
        allowed = True
    elif reason == "request_complete" and request_scope["mode"] == "tasks":
        rows = {r["id"]: r for r in status["tasks"]}
        allowed = all(rows[tid]["status"] == "passed" for tid in request_scope["task_ids"])
    elif reason == "request_complete" and request_scope["mode"] == "review":
        gates = {g["capability_id"]: g for g in status["joint_review"]}
        ready = {a.get("capability_id") for a in continuation["pending_actions"] if a["owner"] == "user" and a["action"] == "joint_review"}
        allowed = all(gates[cid]["status"] == "approved" or cid in ready for cid in request_scope["capability_ids"])
    elif reason == "waiting_human":
        allowed = not agents and not holds and any(a["owner"] == "user" for a in actions)
    elif reason == "waiting_external":
        allowed = not agents and bool(holds)
    elif reason in {"complete", "request_complete"}:
        allowed = continuation["status"] in {"complete", "demo_complete", "scoped_complete", "demo_scoped_complete"}
    else:
        raise ValueError("Unknown end reason")
    control = inspect_control(experiment.root)
    control_pending = bool(control["pending_turn"] or control["journal_error"])
    allowed = allowed and not control_pending
    snapshot = {"binding": binding(experiment, status["tasks"]), "coverage": status["coverage"],
                "joint_review": status["joint_review"], "final_review": status["final_review"],
                "continuation": continuation, "holds": holds, "session_id": session["id"], "scope": request_scope,
                "session_generation": session.get("resume_generation", 0),
                "agent_control": {"pending_turn": control["pending_turn"], "journal_error": control["journal_error"]}}
    digest = runtime.digest(snapshot)
    previous = document["operations"].get(operation_id)
    if previous:
        base.require(previous["input"] == value, "Lifecycle operation ID reused with different input")
        base.check_record(previous["receipt"])
        receipt = base.read_json(previous["receipt"]["path"])
        for item in receipt["evidence"]:
            base.check_record(item)
        return {**receipt, "replayed": True, "allowed": allowed and receipt["allowed"] and receipt["snapshot_digest"] == digest,
                "current": receipt["snapshot_digest"] == digest}
    base.require(session["state"] == "working", "Resume the session before checking its end")
    next_action = agents[0] if agents else next((a for a in actions if a["owner"] == "user"), None)
    if control_pending:
        next_action = {"owner": "agent", "action": "inspect_agent_turn", "command": "agent-status",
                       "replay_saved_commands": False, "reason": control["journal_error"] or "Agent attempt is unresolved"}
    if allowed:
        next_action = ({"owner": "external", "action": "wait_external", "hold_ids": [h["id"] for h in holds]}
                       if reason == "waiting_external" else next_action if reason == "waiting_human" else None)
    result = {"format": FORMAT, "allowed": allowed, "reason": reason, "scope": request_scope,
              "snapshot_digest": digest, "snapshot": snapshot, "evidence": refs, "recorded_at": time.time(),
              "invalid_holds": invalid, "next_action": next_action,
              "migration_complete": continuation["status"] == "complete", "quality_gate_granted": False,
              "enforcement": "host_must_check_exit_status", "automatic_host_hook": False}
    path = experiment.root / "session-end-checks" / (operation_id + ".json")
    base.require(not path.exists(), "End-check receipt already exists")
    runtime.write_json(path, result)
    document["operations"][operation_id] = {"input": value, "receipt": base.file_record(path)}
    state = "paused" if reason == "user_paused" else reason if reason.startswith("waiting_") else "ended"
    if allowed:
        session["state"] = state
        session["end_receipt"] = base.file_record(path)
    append(document, "end_check", allowed=allowed, state=state if allowed else "working", phase=session["phase"],
           reason=reason, receipt=base.file_record(path))
    runtime.write_json(experiment.root / FILE, document)
    return {**result, "receipt_path": str(path)}


def checkpoint(experiment, event):
    if not (experiment.root / FILE).exists():
        return
    document = load(experiment.root)
    session = current(document)
    if session["state"] != "working":
        return
    meaningful = {"task_started", "task_passed", "task_incomplete", "task_failed", "attempt_in_doubt",
                  "attempt_reconciled", "contract_revised", "human_review_recorded"}
    if event in meaningful:
        session["last_progress_at"] = time.time()
    task_id = experiment.state["events"][-1].get("task_id")
    if event == "task_started":
        session["host_phase"] = session["phase"]
        kind = experiment.tasks[task_id]["kind"]
        session["phase"] = {"discover": "discovery", "implement": "implementation", "contract": "assert",
                            "device": "navigate", "visual": "compare", "review": "review_preparation"}.get(kind, kind)
        append(document, "task_phase", state="working", phase=session["phase"], task_id=task_id)
    elif event in {"task_passed", "task_incomplete", "task_failed", "attempt_in_doubt", "attempt_reconciled"}:
        session["phase"] = session.pop("host_phase", session["phase"])
        append(document, "task_phase", state="working", phase=session["phase"], task_id=task_id)
    session["checkpoint"] = {"run_id": experiment.state["run_id"], "contract": experiment.state["contract"],
        "event_sequence": len(experiment.state["events"]), "event": event,
        "joint_reviews": experiment.state["joint_reviews"], "final_approval": experiment.state.get("final_approval"),
        "task_receipts": {tid: saved["receipt"] for tid, saved in experiment.state["tasks"].items() if saved.get("receipt")},
        "active_attempts": {tid: saved["running"] for tid, saved in experiment.state["tasks"].items() if saved.get("running")}}
    runtime.write_json(experiment.root / FILE, document)
