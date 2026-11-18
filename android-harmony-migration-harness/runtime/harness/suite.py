"""Declarative DeviceSuite steps with explicit, evidence-bound interruption recovery."""
import copy
import time
from pathlib import Path

import foundation as base
from foundation import runtime
from execution import ExecutionFailure, failure_record

FORMAT = "migration-device-suite/1"


def path_value(snapshot, path):
    value = snapshot
    for key in path:
        value = value[key]
    return copy.deepcopy(value)


def validate_condition(condition):
    base.require(isinstance(condition, dict) and condition and set(condition) <= {
        "required_controls", "forbidden_controls", "context", "values"}, "Unknown/empty suite condition")
    for key in ("required_controls", "forbidden_controls"):
        base.require(base.ids(condition.get(key, [])), "Condition controls must be unique IDs")
    base.require(isinstance(condition.get("context", {}), dict), "Condition context must be an object")
    base.require(isinstance(condition.get("values", []), list), "Condition values must be a list")
    for item in condition.get("values", []):
        base.require(isinstance(item, dict) and set(item) == {"path", "equals"}, "Condition value needs path/equals")
        validate_path(item["path"])


def validate_path(path):
    base.require(isinstance(path, list) and path and all(base.text(k) or type(k) is int and k >= 0 for k in path),
                 "Observation paths must be nonempty key/index lists")


def matches(snapshot, condition):
    controls = {c["id"] for c in snapshot["controls"] if c.get("visible") is True}
    if not set(condition.get("required_controls", [])) <= controls or set(condition.get("forbidden_controls", [])) & controls:
        return False
    if any(snapshot.get("context", {}).get(k) != v for k, v in condition.get("context", {}).items()):
        return False
    try:
        return all(path_value(snapshot, item["path"]) == item["equals"] for item in condition.get("values", []))
    except (KeyError, IndexError, TypeError):
        return False


def validate(template):
    base.require(isinstance(template, dict) and template.get("format") == FORMAT, "Expected migration-device-suite/1")
    points = template.get("points")
    base.require(isinstance(points, list) and points and all(isinstance(p, dict) for p in points)
                 and base.ids([p.get("acceptance_id") for p in points]),
                 "Suite needs unique acceptance points")
    ids = []
    for point in points:
        base.require(isinstance(point.get("steps", []), list), "Point steps must be a list")
        for step in point.get("steps", []):
            base.require(isinstance(step, dict) and step.get("op") in {"action", "wait", "assert", "capture", "checkpoint"},
                         "Unknown suite step")
            ids.append(step.get("id"))
            if step["op"] == "action":
                base.require(base.text(step.get("selector")), "Action needs a semantic selector")
                validate_condition(step.get("postcondition"))
            elif step["op"] != "capture":
                validate_condition(step.get("condition"))
            if step["op"] == "wait":
                seconds = step.get("timeout_seconds", 10)
                base.require(type(seconds) in (int, float) and 0 < seconds <= 300, "Invalid suite wait timeout")
        observations = point.get("observations")
        base.require(isinstance(observations, list) and observations and all(isinstance(o, dict) for o in observations) and
                     base.ids([o.get("assertion_id") for o in observations]), "Point needs unique observations")
        for item in observations:
            base.require(("path" in item) != ("condition" in item), "Observation needs a path or a condition")
            validate_path(item["path"]) if "path" in item else validate_condition(item["condition"])
    base.require(base.ids(ids), "Step IDs must be globally unique")
    return template


def for_task(template, task, acceptance):
    validate(template)
    base.require({p["acceptance_id"] for p in template["points"]} == set(task["acceptance_ids"]),
                 "Device suite must cover exactly its task's acceptance points")
    for point in template["points"]:
        base.require({o["assertion_id"] for o in point["observations"]} ==
                     {a["id"] for a in acceptance[point["acceptance_id"]]["assertions"]},
                     "Device suite must observe exactly its point's assertions")
    return template


def validate_point(task, raw, point, records, roots):
    if not task.get("device_suite"):
        return
    from contract import artifact
    from execution import validate_device_guard
    path = point.get("suite_binding")
    base.require(any(r["path"] == path for r in records), "Declared suite needs its immutable binding in point evidence")
    binding = base.read_json(path)
    template = base.read_json(artifact(task["device_suite"], roots)["path"])
    base.require(binding.get("template") == template and binding.get("package_sha256") == raw["context"]["package_sha256"],
                 "Declared suite template/package changed")
    expected = binding.get("required_context", {})
    base.require(expected.get("device_id") == raw["context"]["device_id"] and
                 all(raw["context"].get(k) == v for k, v in expected.items()), "Declared suite environment changed")
    base.require(binding.get("engine") == [base.file_record(Path(__file__)),
                 base.file_record(Path(__file__).with_name("execution.py"))], "Declared suite engine changed")
    guarded = {**task, "execution_policy": {"device_guard": expected}}
    validate_device_guard(guarded, raw, point, records)


def run(suite, template, request_path, directory, *, resume=False, reconcile=None):
    template = validate(template)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with runtime.file_lock(directory / "suite.lock", timeout=0) as acquired:
        base.require(acquired, "Device suite is active")
        return _run(suite, template, request_path, directory, resume, reconcile)


def _run(suite, template, request_path, directory, resume, reconcile):
    state_path = directory / "suite-state.json"
    binding_path = directory / "suite-binding.json"
    request = base.read_json(request_path)
    selected = request.get("requested_acceptance_ids", [p["acceptance_id"] for p in template["points"]])
    base.require(base.ids(selected) and selected and set(selected) <= {p["acceptance_id"] for p in template["points"]},
                 "Request selected unknown/empty suite points")
    binding = {"template": template, "request": base.file_record(request_path), "package_sha256": suite.package,
               "required_context": suite.required,
               "engine": [base.file_record(Path(__file__)), base.file_record(Path(__file__).with_name("execution.py"))]}
    if state_path.exists():
        base.require(resume, "Suite already exists; inspect its journal before explicit resume")
        state = base.read_json(state_path)
        base.require(state["binding"] == binding, "Suite request/package/environment/template changed; use a new attempt")
        base.require(base.read_json(binding_path) == binding, "Suite binding changed")
    else:
        base.require(not resume and not reconcile, "No suite checkpoint to resume")
        state = {"format": FORMAT, "binding": binding, "steps": {}, "points": {}, "pending": None}
        base.require(not binding_path.exists() or base.read_json(binding_path) == binding, "Suite binding already exists with different inputs")
        runtime.write_json(binding_path, binding)
        runtime.write_json(state_path, state)
    steps = {step["id"]: step for p in template["points"] for step in p.get("steps", [])}
    for ref in state["steps"].values():
        base.check_record(ref)
        receipt = base.read_json(ref["path"])
        for item in receipt["evidence"]:
            base.check_record(item)
        suite.capture_ids.update(c["capture_id"] for c in receipt["captures"])
    for ref in state["points"].values():
        base.check_record(ref)
        point = base.read_json(ref["path"])
        for item in point["evidence_records"]:
            base.check_record(item)
    if resume:
        snapshot = suite.fresh("resume")
        pending = state.get("pending")
        if pending:
            step = steps[pending]
            if step["op"] == "action":
                base.require(reconcile and reconcile.get("step_id") == pending,
                             "Unfinished step needs explicit observed recovery; never replay a pending action")
                refs = reconcile.get("evidence", [])
                base.require(isinstance(refs, list) and refs, "Recovery needs diagnostic evidence")
                records = [base.file_record(p) for p in refs]
                base.require(matches(snapshot, step["postcondition"]), "Recovery postcondition did not match; restore environment in a new attempt")
                persist_step(suite, directory, state, step, 0, 0, records, recovered=True)
            else:
                state["pending"] = None
                runtime.write_json(state_path, state)
        elif state.get("resume_condition"):
            base.require(matches(snapshot, state["resume_condition"]), "Suite checkpoint no longer matches; restore environment in a new attempt")
    results, failure = [], None
    for point in template["points"]:
        aid = point["acceptance_id"]
        if aid not in selected:
            continue
        if aid in state["points"]:
            results.append(base.read_json(state["points"][aid]["path"])["result"])
            continue
        if failure:
            results.append({"acceptance_id": aid, "status": "not_checked", "reason": "Restore the failed step before continuing"})
            continue
        point_evidence_start = len(suite.evidence)
        actions_before = suite.action_count
        try:
            for step in point.get("steps", []):
                if step["id"] in state["steps"]:
                    continue
                capture_start, evidence_start = len(suite.captures), len(suite.evidence)
                actions_before = suite.action_count
                state["pending"] = step["id"]
                runtime.write_json(state_path, state)
                op = step["op"]
                if op == "action":
                    snapshot = suite.action(step["id"], step["selector"])
                    if not matches(snapshot, step["postcondition"]):
                        raise ExecutionFailure("unknown", "action_postcondition", step["id"],
                                               "Action finished without its postcondition; inspect effects before recovery", step["id"])
                elif op == "wait":
                    suite.wait(step["id"], lambda snap: matches(snap, step["condition"]), timeout_seconds=step.get("timeout_seconds", 10))
                else:
                    snapshot = suite.fresh(step["id"])
                    if op in {"assert", "checkpoint"} and not matches(snapshot, step["condition"]):
                        raise ExecutionFailure("product_failure" if op == "assert" else "environment_changed",
                                               "condition_mismatch", step["id"], "Declared condition did not match", step["id"])
                persist_step(suite, directory, state, step, capture_start, evidence_start)
            snapshot = suite.fresh(aid)
            observations = []
            for item in point["observations"]:
                try:
                    actual = path_value(snapshot, item["path"]) if "path" in item else matches(snapshot, item["condition"])
                except (KeyError, IndexError, TypeError) as error:
                    raise ExecutionFailure("evidence_missing", "observation_missing", aid, str(error), aid) from error
                observations.append({"assertion_id": item["assertion_id"], "actual": actual})
            captures, evidence = list(suite.captures[-1:]), list(suite.evidence[point_evidence_start:])
            for step in point.get("steps", []):
                ref = state["steps"][step["id"]]
                receipt = base.read_json(ref["path"])
                captures.extend(receipt["captures"])
                evidence += [item["path"] for item in receipt["evidence"]] + [ref["path"]]
            journal = directory / (aid + "-suite.json")
            runtime.write_json(journal, {"package_sha256": suite.package, "captures": captures})
            evidence = list(dict.fromkeys(evidence + [str(journal), str(binding_path)]))
            result = {"acceptance_id": aid, "status": "checked", "observations": observations,
                      "evidence": evidence, "suite_receipt": str(journal), "suite_binding": str(binding_path)}
            point_path = directory / (aid + "-point.json")
            runtime.write_json(point_path, {"result": result, "evidence_records": [base.file_record(p) for p in evidence]})
            state["points"][aid] = base.file_record(point_path)
            runtime.write_json(state_path, state)
            results.append(result)
        except ExecutionFailure as error:
            failure = failure_record(error.failure)
            # Only actions actually invoked can have uncertain side effects.
            # Abrupt exits still retain the pending marker written before work.
            if state.get("pending") and (steps[state["pending"]]["op"] != "action"
                                         or suite.action_count == actions_before):
                state["pending"] = None
            runtime.write_json(state_path, state)
            path = directory / (aid + "-failure.json")
            runtime.write_json(path, {"failure": failure, "pending_step": state.get("pending"),
                "step_receipts": {s["id"]: state["steps"][s["id"]] for s in point.get("steps", []) if s["id"] in state["steps"]}})
            results.append({"acceptance_id": aid, "status": "blocked", "reason": str(error), "failure": failure,
                            "evidence": list(dict.fromkeys(suite.evidence[point_evidence_start:] + [str(path)]))})
    result = {"points": results, "context": suite.context, "phase_seconds": suite.phase_seconds,
              "summary": {"checked": sum(p["status"] == "checked" for p in results),
                          "pending_step": state.get("pending"), "failure": failure,
                          "journal": str(state_path), "replay_pending_actions": False}}
    runtime.write_json(directory / "suite-summary.json", result["summary"])
    return result


def persist_step(suite, directory, state, step, capture_start, evidence_start, extra=(), recovered=False):
    path = directory / (step["id"] + "-step.json")
    if path.exists():
        receipt = base.read_json(path)
        base.require(receipt.get("binding_digest") == runtime.digest(state["binding"]) and receipt.get("id") == step["id"],
                     "Orphan step receipt belongs to different inputs")
        for item in receipt["evidence"]:
            base.check_record(item)
    else:
        refs = [base.file_record(p) for p in dict.fromkeys(suite.evidence[evidence_start:])]
        runtime.write_json(path, {"id": step["id"], "op": step["op"], "recorded_at": time.time(),
            "binding_digest": runtime.digest(state["binding"]),
            "captures": suite.captures[capture_start:], "evidence": refs + list(extra), "recovered": recovered})
    state["steps"][step["id"]] = base.file_record(path)
    condition = step.get("postcondition", step.get("condition"))
    if condition:
        state["resume_condition"] = condition
    state["pending"] = None
    runtime.write_json(directory / "suite-state.json", state)
