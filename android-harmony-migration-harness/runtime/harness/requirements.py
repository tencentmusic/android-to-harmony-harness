"""Opt-in execution presets and task-local preparation gaps."""
import foundation as base

PRESETS = {"artifact-build": ("build", "build_manifest"), "guarded-device": ("device", "device_guard"),
           "guarded-visual": ("visual", "device_guard"), "network-transport": (None, "network_exit"),
           "semantic-detector": (None, "detectors")}


def validate(task):
    names = task.get("execution_presets", [])
    base.require(base.ids(names) and set(names) <= set(PRESETS), "Unknown execution preset")
    for name in names:
        kind, _ = PRESETS[name]
        base.require(task["kind"] == kind if kind else task["kind"] in {"contract", "device", "visual"},
                     "Execution preset does not apply to task kind: " + name)


def policy_gaps(task):
    policy = task.get("execution_policy", {})
    return [key for key in policy_requirements(task) if (":" not in key and not policy.get(key)) or
            (":" in key and key.split(":", 1)[1] not in policy.get(key.split(":", 1)[0], {}))]


def policy_requirements(task):
    result = []
    for name in task.get("execution_presets", []):
        field = PRESETS[name][1]
        if field in {"network_exit", "detectors"}:
            result.extend(field + ":" + aid for aid in task["acceptance_ids"])
        else:
            result.append(field)
    return result


def compile_plan(experiment):
    from contract import profile_data
    from project import readiness
    profile, refs = profile_data(experiment.contract)
    prepared = readiness(profile, experiment.contract["roots"], refs[0]["path"], experiment.contract.get("demo", False))
    caps = {row["id"]: row for row in prepared["capabilities"]}
    rows = []
    for task in experiment.tasks.values():
        required = ["capability:" + cid for cid in task.get("project_capability_ids", [])]
        required += ["policy:" + key for key in policy_requirements(task)]
        missing = ["capability:" + cid for cid in task.get("project_capability_ids", []) if caps[cid]["status"] != "verified"]
        missing += ["policy:" + key for key in policy_gaps(task)]
        rows.append({"task_id": task["id"], "required": required,
                     "verified": [key for key in required if key not in missing], "missing": missing,
                     "status": "needs_preparation" if missing else "prepared"})
    return {"tasks": rows, "project_readiness": prepared, "quality_gate_granted": False,
            "note": "Prepared means capability probes are current and required policy fields are configured; self-tests, execution and human approval remain required."}
