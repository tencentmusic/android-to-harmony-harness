"""Evidence-bound delivery for an explicit human-limited scope, separate from gates."""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import foundation as base
from foundation import runtime


def records(paths):
    base.require(isinstance(paths, list) and paths, "Nonempty evidence is required")
    return [base.file_record(Path(p).resolve(strict=True)) for p in paths]


def same_payload(left, right):
    if left["sha256"] == right["sha256"]:
        return True
    # HAP signatures and ZIP metadata may differ; every actual module entry must match.
    try:
        with zipfile.ZipFile(left["path"]) as a, zipfile.ZipFile(right["path"]) as b:
            names = a.namelist()
            if not names or len(set(names)) != len(names) or set(names) != set(b.namelist()) or len(names) != len(b.namelist()):
                return False
            if not all(n.endswith((".hap", ".hsp")) for n in names):
                return False
            for name in names:
                with zipfile.ZipFile(io.BytesIO(a.read(name))) as x, zipfile.ZipFile(io.BytesIO(b.read(name))) as y:
                    entries = x.namelist()
                    if len(set(entries)) != len(entries) or set(entries) != set(y.namelist()) or len(entries) != len(y.namelist()):
                        return False
                    if any(x.read(n) != y.read(n) for n in entries):
                        return False
            return True
    except (OSError, ValueError, zipfile.BadZipFile):
        return False


def binding(experiment, task_id, status):
    row = next((r for r in status["tasks"] if r["id"] == task_id), {})
    base.require(row.get("status") == "passed" and experiment.tasks[task_id]["kind"] == "build",
                 "A current passed build is required for scoped delivery")
    base.require(all(g["status"] == "approved" for g in status["joint_review"]), "Joint approvals must remain valid")
    receipt = experiment.state["tasks"][task_id]["receipt"]
    return {"contract": experiment.state["contract"], "build_task_id": task_id,
            "build_receipt": receipt, "input_digest": experiment.fingerprint(experiment.tasks[task_id])}


def state(experiment, status=None):
    pointer = experiment.root / "delivery-closeout.json"
    if not pointer.exists():
        return None
    try:
        current = base.read_json(pointer)
        base.check_record(current["prepared"])
        packet = base.read_json(current["prepared"]["path"])
        base.require(packet["binding"] == binding(experiment, packet["binding"]["build_task_id"], status or experiment.status()),
                     "Delivery source, build or contract changed")
        for record in packet["evidence"]:
            base.check_record(record)
        result = {"status": "awaiting_human", "scope": "user_limited_delivery", "input_digest": runtime.digest(packet),
                  "report": packet["report"], "candidate": packet["candidate"], "checks": packet["checks"],
                  "excluded_acceptance_ids": packet["excluded_acceptance_ids"], "limitations": packet["limitations"],
                  "full_gate_unchanged": True}
        if current.get("decision"):
            base.check_record(current["decision"])
            decision = base.read_json(current["decision"]["path"])
            base.require(decision["input_digest"] == result["input_digest"], "Delivery decision is stale")
            for record in decision["evidence"]:
                base.check_record(record)
            result["status"] = ("demo_scoped_complete" if experiment.contract.get("demo") else "scoped_complete") if decision["decision"] == "approved" else "rejected"
        return result
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"status": "stale", "scope": "user_limited_delivery", "reason": str(error), "full_gate_unchanged": True}


def record(experiment, operation_id, value):
    base.require(base.text(operation_id), "Operation ID is required")
    directory = experiment.root / "delivery-closeouts"
    directory.mkdir(exist_ok=True)
    operation = directory / (runtime.digest(operation_id) + ".json")
    with experiment.locked():
        if operation.exists():
            old = base.read_json(operation)
            base.require(old["request_digest"] == runtime.digest(value), "Closeout operation reused with different input")
            return state(experiment)
        status = experiment.status()
        action = value.get("action")
        if action == "prepare":
            authorization = value.get("authorization", {})
            base.require(authorization.get("actor_type") == "human" and base.text(authorization.get("instruction")),
                         "Explicit human scope instruction is required; partial test success is not authorization")
            checks = value.get("checks")
            base.require(isinstance(checks, list) and checks, "Scoped checks are required")
            base.require(base.ids([c.get("id") for c in checks]), "Unique scoped check IDs are required")
            all_ids = set(experiment.acceptance)
            included = set()
            evidence = records(authorization.get("evidence"))
            for check in checks:
                base.require(check.get("status") == "passed" and base.text(check.get("summary")), "Every scoped check must be completed")
                base.require(base.ids(check.get("acceptance_ids", [])) and check["acceptance_ids"], "Map checks to original acceptance IDs")
                included.update(check["acceptance_ids"])
                evidence += records(check.get("evidence"))
            excluded = value.get("excluded_acceptance_ids", [])
            base.require(base.ids(excluded) and set(excluded).isdisjoint(included) and included | set(excluded) == all_ids,
                         "Every original acceptance ID must be mapped or explicitly excluded, without overlap")
            base.require(base.text(value.get("exclusion_reason")), "Explain the user-authorized exclusions")
            review = value.get("agent_review", {})
            base.require(review.get("findings") == [] and base.text(review.get("summary")), "Agent review must finish before requesting delivery approval")
            evidence += records(review.get("evidence"))
            base.require(base.text(value.get("cleanup")), "Record final cleanup and installed artifact")
            report = base.file_record(Path(value["report"]).resolve(strict=True))
            base.require(Path(report["path"]).suffix.lower() == ".html", "Scoped delivery needs a concrete HTML report")
            from handoff import current as handoff_state
            handoff = handoff_state(experiment, status)
            base.require(handoff["status"] == "current" and handoff["html_path"] == report["path"],
                         "Generate the current developer handoff HTML before preparing scoped delivery")
            current_binding = binding(experiment, value["build_task_id"], status)
            candidate = base.file_record(Path(value["candidate"]).resolve(strict=True))
            build = experiment.receipt(value["build_task_id"])["package"]
            base.check_record(build)
            base.require(same_payload(candidate, build), "Tested package does not match current built code")
            evidence += [report, candidate]
            packet = {**value, "binding": current_binding, "candidate": candidate, "report": report,
                      "evidence": list({r["path"]: r for r in evidence}.values()), "created_at": time.time()}
            packet_path = directory / (runtime.digest(packet) + "-packet.json")
            runtime.write_json(packet_path, packet)
            current = {"prepared": base.file_record(packet_path)}
        elif action == "decide":
            prepared = state(experiment, status)
            base.require(prepared and prepared["status"] == "awaiting_human", "Current prepared delivery is required")
            actor = "demo" if experiment.contract.get("demo") else "human"
            base.require(value.get("actor_type") == actor and base.text(value.get("reviewer")) and base.text(value.get("summary")),
                         "Only a recorded human delivery decision is accepted")
            base.require(value.get("input_digest") == prepared["input_digest"], "Delivery decision digest does not match")
            base.require(value.get("decision") in {"approved", "rejected"}, "Invalid delivery decision")
            decision = {**value, "evidence": records(value.get("evidence")), "created_at": time.time()}
            decision_path = directory / (runtime.digest(decision) + "-decision.json")
            runtime.write_json(decision_path, decision)
            current = base.read_json(experiment.root / "delivery-closeout.json")
            current["decision"] = base.file_record(decision_path)
        else:
            raise ValueError("Closeout action must be prepare or decide")
        runtime.write_json(experiment.root / "delivery-closeout.json", current)
        runtime.write_json(operation, {"request_digest": runtime.digest(value), "record": current})
        return state(experiment, status)


def continuation(delivery, original):
    if delivery is None:
        return original
    complete = delivery["status"] in {"scoped_complete", "demo_scoped_complete"}
    waiting = delivery["status"] == "awaiting_human"
    action = None if complete else {"action": "scoped_final_review" if waiting else "repair_scoped_delivery",
        "owner": "user" if waiting else "agent", "command": "closeout", "reason": delivery.get("reason", delivery["status"])}
    return {**original, "scope": "user_limited_delivery", "status": delivery["status"],
            "can_end_turn": complete or waiting, "next_action": action, "pending_actions": [action] if action else [],
            "stop_reason": "Explicit scoped delivery approved" if complete else ("Only scoped final human review remains" if waiting else None),
            "delivery": delivery, "whole_feature_continuation": original,
            "note": "Explicit human scope only. Original acceptance results, budgets and verify/delivery_ready are unchanged."}
