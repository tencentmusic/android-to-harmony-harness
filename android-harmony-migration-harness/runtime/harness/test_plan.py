"""Compile change-driven scenarios into the existing point execution protocol."""
import copy
from collections import Counter

import foundation as base
from foundation import runtime

require, text, ids, index = base.require, base.text, base.ids, base.index
VERSION = 1


def covered_states(point):
    return point.get("covered_state_ids", [point["state_id"]] if point.get("state_id") else [])


def references(plan):
    items = plan["changes"] + plan.get("excluded_states", [])
    items += (plan.get("revision") or {}).get("retirements", [])
    return [ref for item in items for ref in item["sources"]]


def scope_plan(plan, capability_ids):
    if plan is None:
        return None
    revision = plan.get("revision")
    scoped_revision = None
    if revision:
        replaced = [r for r in revision.get("replacements", []) if r["capability_id"] in capability_ids]
        retired = [r for r in revision.get("retirements", []) if r["capability_id"] in capability_ids]
        if replaced or retired:
            scoped_revision = {"reason": revision["reason"], "replacements": replaced, "retirements": retired}
    return {"version": plan["version"],
            "changes": [c for c in plan["changes"] if c["capability_id"] in capability_ids],
            "scenarios": [s for s in plan["scenarios"] if s["capability_id"] in capability_ids],
            "excluded_states": [s for s in plan.get("excluded_states", []) if s["capability_id"] in capability_ids],
            "revision": scoped_revision}


def compile_points(contract, plan):
    require(isinstance(plan, dict) and type(plan.get("version")) is int and plan["version"] == VERSION,
            "Unsupported test_plan version")
    caps = index(contract.get("capabilities"), "capabilities")
    revision = plan.get("revision")
    if revision is not None:
        require(isinstance(revision, dict) and text(revision.get("previous_digest")) and text(revision.get("reason")),
                "Plan revision needs the previous digest and a reason")
        for key in ("replacements", "retirements"):
            require(isinstance(revision.get(key), list), "Revision entries must be lists")
            for row in revision[key]:
                require(isinstance(row, dict) and row.get("capability_id") in caps and text(row.get("reason")),
                        "Revision entry needs a capability and reason")
                if key == "retirements":
                    require(isinstance(row.get("sources"), list) and row["sources"], "Retirement needs evidence")
    states = index(contract.get("states"), "states")
    tasks = index(contract.get("tasks"), "tasks")
    changes = index(plan.get("changes"), "plan changes")
    scenarios = index(plan.get("scenarios"), "plan scenarios")
    for change in changes.values():
        require(change.get("capability_id") in caps, "Change needs a capability")
        require(all(text(change.get(k)) for k in ("before", "after")), "Change needs before and after behavior")
        require(isinstance(change.get("sources"), list) and change["sources"], "Change needs source evidence")
    points, used_changes, selected = [], set(), set()
    for scenario in scenarios.values():
        cid = scenario.get("capability_id")
        require(cid in caps, "Scenario needs a capability")
        change_ids = scenario.get("change_ids")
        require(ids(change_ids) and change_ids and set(change_ids) <= changes.keys(), "Scenario needs known changes")
        require(all(changes[c]["capability_id"] == cid for c in change_ids), "Scenario crosses change capabilities")
        state_ids = scenario.get("state_ids")
        require(ids(state_ids) and state_ids and set(state_ids) <= states.keys(), "Scenario needs known states")
        require(all(states[s]["capability_id"] == cid for s in state_ids), "Scenario crosses state capabilities")
        require(all(text(scenario.get(k)) for k in ("behavior", "risk", "grouping_reason")),
                "Scenario needs behavior, risk and implementation grouping reason")
        require(type(scenario.get("observable_ui")) is bool, "Scenario must declare observable_ui")
        verifications = scenario.get("verifications")
        require(isinstance(verifications, list) and verifications, "Scenario needs a verification")
        kinds = set()
        for verification in verifications:
            require(isinstance(verification, dict), "Verification must be an object")
            require(set(verification) <= {"kind", "task_id", "reason", "assertions", "visual_comparison"},
                    "Unknown verification fields")
            kind, tid = verification.get("kind"), verification.get("task_id")
            require(kind in base.TEST_KINDS and kind not in kinds, "Choose each verification kind at most once per scenario")
            require(tid in tasks and tasks[tid]["kind"] == kind and cid in tasks[tid]["capability_ids"],
                    "Verification needs a matching execution task")
            require(text(verification.get("reason")), "Verification needs a method selection reason")
            require(kind != "visual" or scenario["observable_ui"], "Background-only scenarios cannot request visual checks")
            if "visual_comparison" in verification:
                require(kind == "visual" and isinstance(verification["visual_comparison"], dict),
                        "visual_comparison requires a visual verification binding")
            checks = index(verification.get("assertions"), "verification assertions")
            for assertion in checks.values():
                require(assertion.get("operator") in {"equal", "truthy"} and "expected" in assertion,
                        "Unsupported or incomplete assertion")
                require(assertion["operator"] != "truthy" or assertion["expected"] is True,
                        "truthy requires expected=true")
            points.append({"id": scenario["id"] + "-" + kind, "capability_id": cid, "kind": kind,
                           "scenario_id": scenario["id"], "state_id": state_ids[0],
                           "covered_state_ids": list(state_ids), "required": True,
                           "behavior": scenario["behavior"], "assertions": copy.deepcopy(list(checks.values()))})
            kinds.add(kind)
        used_changes.update(change_ids)
        selected.update(state_ids)
    require(used_changes == changes.keys(), "Every declared change needs a relevant scenario")
    excluded = plan.get("excluded_states", [])
    require(isinstance(excluded, list), "excluded_states must be a list")
    excluded_ids = set()
    for item in excluded:
        require(isinstance(item, dict), "State exclusion must be an object")
        sid = item.get("state_id")
        require(sid in states and sid not in excluded_ids and sid not in selected, "Invalid or overlapping state exclusion")
        require(item.get("capability_id") == states[sid]["capability_id"], "Exclusion capability mismatch")
        require(item.get("reason_code") in {"unchanged", "not_migrated"} and text(item.get("reason")),
                "Exclusion must explain unchanged or non-migrated behavior")
        require(isinstance(item.get("sources"), list) and item["sources"], "Exclusion needs source evidence")
        excluded_ids.add(sid)
    require(selected | excluded_ids == states.keys(), "Account for every inventory state in scenarios or exclusions")
    return points


def visual_bindings(plan):
    result = {}
    for scenario in plan["scenarios"]:
        for verification in scenario["verifications"]:
            if "visual_comparison" in verification:
                result.setdefault(verification["task_id"], {})[scenario["id"] + "-visual"] = copy.deepcopy(
                    verification["visual_comparison"])
    return result


def compile_contract(raw, plan):
    """Only generate selected methods; unused test tasks are removed with dependencies preserved."""
    contract = copy.deepcopy(raw)
    contract["test_plan"] = copy.deepcopy(plan)
    contract["acceptance"] = compile_points(contract, plan)
    owners = {s["id"] + "-" + v["kind"]: v["task_id"]
              for s in plan["scenarios"] for v in s["verifications"]}
    tasks = index(contract["tasks"], "tasks")
    base.order_tasks(tasks)
    for task in tasks.values():
        if task["kind"] in base.TEST_KINDS:
            task["acceptance_ids"] = [aid for aid, tid in owners.items() if tid == task["id"]]
    for tid, bindings in visual_bindings(plan).items():
        tasks[tid]["visual_comparison"] = bindings
    unused = {tid for tid, task in tasks.items() if task["kind"] in base.TEST_KINDS and not task["acceptance_ids"]}

    def dependencies(tid):
        if tid not in unused:
            return {tid}
        return {ancestor for parent in tasks[tid]["depends_on"] for ancestor in dependencies(parent)}

    contract["tasks"] = [task for tid, task in tasks.items() if tid not in unused]
    for task in contract["tasks"]:
        task["depends_on"] = sorted({ancestor for tid in task["depends_on"] for ancestor in dependencies(tid)})
    return contract


def summary(contract, points=()):
    plan = contract.get("test_plan")
    if plan is None:
        return None
    outcomes = {p["acceptance_id"]: p["status"] for p in points}
    scenarios = []
    for scenario in plan["scenarios"]:
        aids = [scenario["id"] + "-" + v["kind"] for v in scenario["verifications"]]
        statuses = [outcomes.get(a, "not_checked") for a in aids]
        status = next((s for s in ("failed", "blocked", "not_checked") if s in statuses), "passed")
        scenarios.append({"id": scenario["id"], "behavior": scenario["behavior"], "risk": scenario["risk"],
                          "state_ids": scenario["state_ids"], "acceptance_ids": aids,
                          "methods": [v["kind"] for v in scenario["verifications"]], "status": status})
    return {"version": VERSION, "digest": runtime.digest(plan), "changes": len(plan["changes"]), "scenarios": len(scenarios),
            "acceptance_points": len(contract["acceptance"]),
            "assertions": sum(len(a["assertions"]) for a in contract["acceptance"]),
            "methods": dict(Counter(a["kind"] for a in contract["acceptance"])),
            "execution_suites": sum(t["kind"] in base.TEST_KINDS for t in contract["tasks"]),
            "excluded_states": plan.get("excluded_states", []), "scenario_results": scenarios,
            "revision": plan.get("revision")}


def validate_revision(old, new, failed_ids):
    old_plan, new_plan = old.get("test_plan"), new.get("test_plan")
    require(old_plan is not None and new_plan is not None, "Plan mode cannot be changed inside an existing run")
    if old_plan == new_plan and old["acceptance"] == new["acceptance"]:
        return
    revision = new_plan.get("revision")
    require(isinstance(revision, dict) and revision.get("previous_digest") == runtime.digest(old_plan)
            and text(revision.get("reason")), "Plan revision needs the previous digest and a reason")
    previous = index(old["acceptance"], "old acceptance")
    current = index(new["acceptance"], "new acceptance")
    require(all(previous[aid] == current[aid] for aid in previous.keys() & current.keys()),
            "Changed checks need new IDs; existing point definitions are immutable")
    removed = previous.keys() - current.keys()
    replacements = revision.get("replacements", [])
    retirements = revision.get("retirements", [])
    require(isinstance(replacements, list) and isinstance(retirements, list), "Revision needs replacement and retirement lists")
    accounted = set()
    for row in replacements + retirements:
        require(isinstance(row, dict), "Revision entry must be an object")
        aid = row.get("acceptance_id")
        require(aid in removed and aid not in accounted and text(row.get("reason")), "Invalid revision entry")
        require(row.get("capability_id") == previous[aid]["capability_id"], "Revision entry capability mismatch")
        accounted.add(aid)
        if row in replacements:
            targets = row.get("replacement_ids")
            require(ids(targets) and targets and set(targets) <= current.keys(), "Replacement needs current points")
            peers = [current[target] for target in targets]
            require(all(p["kind"] == previous[aid]["kind"] and p["capability_id"] == previous[aid]["capability_id"] for p in peers),
                    "Replacement cannot weaken method or capability coverage")
            require(set(covered_states(previous[aid])) <= {s for p in peers for s in covered_states(p)},
                    "Replacement cannot lose covered states")
            require(all(check in [c for p in peers for c in p["assertions"]] for check in previous[aid]["assertions"]),
                    "Replacement cannot drop or weaken assertions")
        else:
            require(aid not in failed_ids, "A failed point cannot be retired; fix and retest or retain equivalent checks")
            require(isinstance(row.get("sources"), list) and row["sources"], "Retirement needs diff or scope evidence")
    require(accounted == removed, "Every removed point needs a replacement or evidenced retirement")
