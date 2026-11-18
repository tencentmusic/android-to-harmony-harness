"""Bind deterministic visual comparisons to reviewed states and device receipts."""
import sys
from pathlib import Path

import foundation as base
from test_plan import covered_states


def require_real_binding(contract, task):
    base.require(contract.get("demo") is True or task.get("kind") != "visual"
                 or isinstance(task.get("visual_comparison"), dict) and task["visual_comparison"],
                 "Real visual tasks require visual_comparison; revise the contract to bind current device captures")


def adapter_command():
    return {"cwd": "tools", "argv": [sys.executable, str(Path(__file__).with_name("visual_adapter.py")),
                                     "{request}", "{result}"], "timeout_seconds": 120}


def validate_binding(task, acceptance, states, tasks):
    if "visual_comparison" not in task:
        return
    configured = task["visual_comparison"]
    base.require(task["kind"] == "visual" and not task.get("device_suite") and not task.get("project_command"),
                 "Built-in visual comparison requires a dedicated visual task")
    base.require(isinstance(configured, dict) and set(configured) == set(task["acceptance_ids"]),
                 "Configure every owned visual comparison point")
    for aid, spec in configured.items():
        base.require(isinstance(spec, dict) and set(spec) <= {"assertion_id", "baseline_index", "regions", "capture_from"},
                     "Unknown visual comparison fields")
        point = acceptance[aid]
        state_ids = covered_states(point)
        base.require(len(state_ids) == 1, "Built-in visual comparison needs one state per point")
        assertions = {a["id"]: a for a in point["assertions"]}
        assertion = assertions.get(spec.get("assertion_id"), {})
        base.require(assertion.get("expected") is True and assertion.get("operator") in {"equal", "truthy"},
                     "Visual comparison must bind a required expected=true assertion")
        index = spec.get("baseline_index", 0)
        baselines = states[next(iter(state_ids))]["baselines"]
        base.require(type(index) is int and index >= 0 and (index < len(baselines) if baselines else index == 0),
                     "Visual comparison baseline_index is invalid")
        source = spec.get("capture_from")
        base.require(isinstance(source, dict) and set(source) <= {"task_id", "acceptance_id", "image_name"},
                     "Visual comparison needs capture_from")
        tid, source_aid = source.get("task_id"), source.get("acceptance_id")
        base.require(tid in task["depends_on"] and tasks[tid]["kind"] == "device",
                     "Visual capture must come from a direct device dependency")
        base.require(source_aid in tasks[tid]["acceptance_ids"] and
                     state_ids == covered_states(acceptance[source_aid]),
                     "Visual capture must cover the same declared state")
        if "image_name" in source:
            name = source["image_name"]
            base.require(base.text(name) and Path(name).name == name and
                         Path(name).suffix.lower() in {".png", ".jpg", ".jpeg"}, "Invalid visual capture image_name")
        if "regions" in spec:
            from visual import validate_regions
            validate_regions(spec["regions"])


def validate_point(experiment, task, raw, point, records):
    from visual import verify_comparison_report
    from visual_adapter import resolve_capture

    aid = point["acceptance_id"]
    spec = task["visual_comparison"][aid]
    record = point.get("visual_comparison")
    base.require(isinstance(record, dict) and record in records, "Visual point needs its comparison report evidence")
    base.check_record(record)
    report = verify_comparison_report(record["path"])
    base.require(report["diagnostic"] is False and report["gate_eligible"] is True,
                 "Diagnostic image comparison cannot satisfy native acceptance")
    state_id = experiment.acceptance[aid]["state_id"]
    base.require(report["state_id"] == state_id, "Visual comparison state differs from acceptance")
    baseline = experiment.states[state_id]["baselines"][spec.get("baseline_index", 0)]
    from contract import baseline_artifact
    expected = baseline_artifact(baseline, experiment.contract["roots"])
    actual = report["inputs"]["baseline"]
    base.require(all(actual.get(k) == expected[k] for k in ("path", "sha256")),
                 "Visual report uses a different declared baseline")
    request = {"format": experiment.contract["format"], "demo": experiment.contract.get("demo", False),
               "package": experiment.package(task), "dependency_receipts": {
                   tid: experiment.state["tasks"][tid]["receipt"] for tid in task["depends_on"]}}
    source = resolve_capture(request, spec)
    base.require(source["context"] == raw.get("context"), "Visual comparison environment differs from device capture")
    capture = report["inputs"]["capture"]
    base.require(all(capture.get(k) == source["capture"][k] for k in ("path", "sha256")),
                 "Visual report must use the current device dependency capture")
    if "regions" in spec:
        regions = spec["regions"]
    else:
        width, height = actual["width"], actual["height"]
        regions = {"viewport": {"width": width, "height": height}, "masks": [],
                   "required_rois": [{"id": "viewport", "x": 0, "y": 0, "width": width, "height": height}]}
    base.require(report["regions"] == regions, "Visual comparison regions differ from reviewed policy")
    required = [*source["evidence"], *report["artifacts"].values()]
    required += [{k: v[k] for k in ("path", "sha256")} for v in report["inputs"].values()]
    base.require(all(r in records for r in required), "Visual comparison is missing linked image/device evidence")
    observations = [o for o in point.get("observations", []) if o.get("assertion_id") == spec["assertion_id"]]
    base.require(len(observations) == 1 and observations[0].get("actual") is (report["status"] == "passed"),
                 "Visual observation differs from recomputed image comparison")
