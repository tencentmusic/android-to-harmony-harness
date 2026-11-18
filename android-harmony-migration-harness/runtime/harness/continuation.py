"""Read-only next-step advice; never changes evidence, gates or executor budgets."""
from __future__ import annotations
import foundation as base
from foundation import runtime
from closeout import state as delivery_state, continuation as delivery_continuation
from handoff import current as handoff_state


def plan(status, ready_review_scopes=(), final_report_ready=False):
    """Interpret the whole feature, including work outside a selected run batch.

    Task/point `blocked` is a dependency result, not proof of an external blocker.
    The current protocol has no evidence-bound external-hold record, so repairable
    and unknown states conservatively require agent diagnosis.
    """
    rows = {row["id"]: row for row in status["tasks"]}
    gates = {gate["capability_id"]: gate for gate in status["joint_review"]}
    actions, blocked = [], []
    seen_scopes = set()

    def add(action, owner="agent", task=None, **details):
        item = {"action": action, "owner": owner, **details}
        if task is not None:
            item["task_id"] = task["id"]
            item["task_status"] = task["status"]
            item["pending_acceptance_ids"] = [p["acceptance_id"] for p in status["points"]
                if p["task_id"] == task["id"] and p["status"] != "passed"]
        actions.append(item)

    def roots(tid, visited=None):
        visited = set() if visited is None else visited
        if tid in visited or tid not in rows:
            return [tid]
        row = rows[tid]
        if row["status"] != "blocked" or not row.get("dependencies"):
            return [tid]
        return sorted({root for dep in row["dependencies"] for root in roots(dep, visited | {tid})})

    for row in rows.values():
        state = row["status"]
        if state == "passed":
            continue
        if state == "blocked" and row.get("dependencies"):
            blocked.append({"task_id": row["id"], "dependencies": row["dependencies"],
                            "root_task_ids": roots(row["id"]), "external_block_confirmed": False})
        elif state in {"ready", "stale"}:
            add("run_task", task=row, command="run", reason="Execute current inputs; reuse valid dependencies")
        elif state == "agent_required":
            add("complete_agent_task", task=row, command="record", reason="Do the work and record current evidence")
        elif state == "needs_diagnosis":
            problems = row.get("problems", [])
            add("diagnose_task", task=row, command="inspect", reason=row.get("reason", state), problems=problems,
                recovery_actions=sorted({p["recovery_action"] for p in problems}),
                follow_up="Follow classified recovery and record new diagnostic evidence/strategy before retry")
        elif state == "input_missing":
            add("repair_inputs", task=row, command="inspect", reason=row.get("reason", state),
                failure=row.get("failure"),
                follow_up="Restore referenced inputs or verify only missing project capabilities; self-test broken detectors before business diagnosis")
        elif state == "budget_exhausted":
            add("diagnose_budget", task=row, command="inspect", reason=row.get("reason", state),
                follow_up="Record a new strategy and evidence before any extend-budget/retry; never reset failures")
        elif state == "in_doubt":
            add("inspect_interrupted_attempt", task=row, command="inspect", reason=row.get("reason", state),
                replay_saved_commands=False,
                follow_up="Check process and existing result; reconcile completed evidence before considering retry")
        elif state == "awaiting_human" and row.get("review_scopes"):
            for cid in row["review_scopes"]:
                if cid in seen_scopes:
                    continue
                seen_scopes.add(cid)
                gate = gates.get(cid, {})
                if gate.get("status") == "rejected" or (gate.get("status") == "stale" and cid not in ready_review_scopes):
                    add("repair_joint_review", capability_id=cid, command="review-packet",
                        reason=gate.get("reason", "Address rejected or changed materials before requesting review"))
                elif gate.get("status") in {"awaiting_human", "stale"} and cid in ready_review_scopes:
                    add("joint_review", owner="user", capability_id=cid, command="review-packet",
                        reason="Present the current joint HTML; register any existing valid human decision first")
                elif gate.get("status") == "awaiting_human":
                    add("prepare_joint_review", capability_id=cid, command="review-packet",
                        reason="Create and validate a current HTML packet before requesting human review")
                else:
                    add("diagnose_review_state", capability_id=cid, command="status", reason="Inconsistent review state")
        else:
            add("diagnose_task_state", task=row, command="inspect", reason="Unclassified state: " + state)

    all_passed = bool(rows) and all(r["status"] == "passed" for r in rows.values())
    all_points = bool(status["points"]) and all(p["status"] == "passed" for p in status["points"])
    final = status["final_review"]["status"]
    complete = (all_passed and all_points and final == "approved" and final_report_ready
                and status["status"] in {"delivery_ready", "demo_only"})
    if all_passed and not all_points:
        add("diagnose_point_coverage", command="coverage", reason="Task completion disagrees with required point results")
    elif all_passed and all_points and not complete:
        if not final_report_ready:
            add("prepare_developer_handoff", command="handoff",
                reason="Prepare current developer HTML in the Harmony project and finish knowledge backfill")
        elif final in {"awaiting_human", "stale"}:
            add("final_review", owner="user", command="approve",
                reason="Present current candidate and evidence; record the actual final human decision")
        else:
            add("address_final_review", command="status", reason="Resolve final review: " + final)

    # Actionable independent work comes before waiting gates and dependency rows.
    actions.sort(key=lambda item: item["owner"] != "agent")
    agents = [a for a in actions if a["owner"] == "agent"]
    if not complete and not actions:
        add("diagnose_dependency_graph", command="status", reason="No executable frontier was identified")
        agents = actions[:]
    mode = ("demo_complete" if status["status"] == "demo_only" else "complete") if complete else (
        "must_continue" if agents else "awaiting_human")
    return {"status": mode, "can_end_turn": not agents,
            "stop_reason": None if agents else ("All gates completed" if complete else "Only human review remains on the executable frontier"),
            "next_action": actions[0] if actions else None, "pending_actions": actions,
            "blocked_tasks": blocked, "reusable_task_ids": [tid for tid, row in rows.items() if row["status"] == "passed"],
            "passed_acceptance_ids": [p["acceptance_id"] for p in status["points"] if p["status"] == "passed"],
            "scope": "whole_feature", "background_worker": False, "replay_saved_commands": False,
            "note": "Advice only: execute authorized next steps in the active agent turn. A nonzero exit or a blocked descendant is not permission to stop. Explicit user pause and truly exhausted independent work take precedence; this protocol does not infer external holds from error text."}


def advice(experiment, status=None):
    status = experiment.status() if status is None else status
    waiting = {g["capability_id"]: g.get("input_digest") for g in status["joint_review"]
               if g["status"] in {"awaiting_human", "stale"} and g.get("input_digest")}
    ready = set()
    if waiting:
        for file in (experiment.root / "review-packets").glob("*/packet.json"):
            try:
                packet = base.read_json(file)
                html = file.parent / "index.html"
                if not html.is_file() or html.stat().st_size == 0:
                    continue
                for gate in packet["joint_review"]:
                    cid = gate["capability_id"]
                    if cid not in waiting or cid in ready or gate.get("input_digest") != waiting[cid]:
                        continue
                    archive_path = file.parent / cid / "snapshot.json"
                    archive = base.read_json(archive_path)
                    if runtime.digest(archive["snapshot"]) == waiting[cid]:
                        experiment.check_review_archive(base.file_record(archive_path))
                        ready.add(cid)
            except (OSError, ValueError, KeyError, TypeError):
                continue
    handoff = handoff_state(experiment, status)
    delivery = delivery_state(experiment, status)
    result = delivery_continuation(delivery, plan(status, ready, handoff["status"] == "current"))
    if delivery and delivery["status"] in {"awaiting_human", "scoped_complete", "demo_scoped_complete"}:
        action = None
        if handoff["status"] != "current":
            action = {"action": "prepare_developer_handoff", "owner": "agent", "command": "handoff",
                      "reason": handoff.get("reason", "Prepare developer HTML in the Harmony project")}
        elif delivery["report"]["path"] != handoff["html_path"]:
            action = {"action": "prepare_scoped_delivery", "owner": "agent", "command": "closeout",
                      "reason": "Bind scoped delivery to the current developer handoff HTML"}
        if action:
            result.update(status="must_continue", can_end_turn=False, stop_reason=None,
                          next_action=action, pending_actions=[action])
    result["developer_handoff"] = handoff
    return result


def command_error(reason):
    return {"status": "must_continue", "can_end_turn": False, "stop_reason": None,
            "next_action": {"action": "diagnose_command_error", "owner": "agent", "reason": str(reason),
                "follow_up": "Inspect inputs and current run/results before retry; the preceding operation may already have completed"},
            "replay_saved_commands": False, "background_worker": False}
