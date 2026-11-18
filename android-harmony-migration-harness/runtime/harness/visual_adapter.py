"""Compare Android baselines with captures from current device task receipts.

Usage: python visual_adapter.py REQUEST RESULT
"""
import copy
import sys
import time
from pathlib import Path

import foundation as base
from foundation import runtime
from contract import FORMAT, baseline_artifact
from test_plan import covered_states
from visual import compare_images


def _records(values):
    base.require(isinstance(values, list) and values, "Native capture needs evidence records")
    for record in values:
        base.require(isinstance(record, dict) and set(record) == {"path", "sha256"}, "Invalid capture evidence record")
        base.check_record(record)
    return values


def _unique(records):
    return list({record["path"]: record for record in records}.values())


def resolve_capture(request, spec):
    """Resolve a unique image and its real context from a passed explicit dependency."""
    base.require(isinstance(request, dict) and request.get("format") == FORMAT, "Unsupported visual request format")
    base.require(isinstance(spec, dict) and isinstance(spec.get("capture_from"), dict),
                 "Visual comparison needs capture_from")
    source = spec["capture_from"]
    base.require(set(source) <= {"task_id", "acceptance_id", "image_name"}, "Unknown native capture source fields")
    tid, aid = source.get("task_id"), source.get("acceptance_id")
    base.require(base.identifier(tid) and base.identifier(aid), "Native capture needs task_id and acceptance_id")
    dependencies = request.get("dependency_receipts")
    base.require(isinstance(dependencies, dict) and tid in dependencies,
                 "Capture task must be an explicit current dependency")
    dependency = dependencies[tid]
    _records([dependency])
    receipt = base.read_json(dependency["path"])
    base.require(isinstance(receipt, dict) and receipt.get("format") == FORMAT
                 and receipt.get("task_id") == tid and receipt.get("kind") == "device",
                 "Capture dependency must be a matching device receipt")
    inputs = receipt.get("inputs")
    base.require(isinstance(inputs, dict) and inputs.get("format") == FORMAT
                 and isinstance(inputs.get("task"), dict) and inputs["task"].get("id") == tid
                 and inputs["task"].get("kind") == "device"
                 and receipt.get("input_digest") == runtime.digest(inputs), "Invalid native receipt input binding")
    base.require(receipt.get("accepted") is True, "Capture dependency has not passed")
    context = receipt.get("context")
    base.require(isinstance(context, dict) and all(base.text(context.get(key)) for key in
                 ("device_id", "system_version", "app_id", "account_ref")), "Native capture context is incomplete")
    package = request.get("package")
    base.require(isinstance(package, dict), "Visual request needs the current package")
    base.check_record(package)
    base.require(context.get("package_sha256") == package["sha256"], "Native capture belongs to another package")
    if request.get("demo") is True:
        base.require(context.get("synthetic") is True, "Demo captures must be marked synthetic")
    else:
        base.require(context.get("synthetic") is False and context.get("normal_entry") is True,
                     "Native capture requires normal, non-injected entry")
    results = receipt.get("point_results")
    base.require(isinstance(results, list), "Native receipt needs point_results")
    points = [point for point in results if isinstance(point, dict) and point.get("acceptance_id") == aid]
    base.require(len(points) == 1 and points[0].get("status") == "passed", "Native capture point has not passed uniquely")
    point_evidence = _records(points[0].get("evidence"))
    receipt_evidence = _records(receipt.get("evidence"))
    base.require(all(record in receipt_evidence for record in point_evidence),
                 "Native point evidence is missing from its receipt")
    images = [record for record in point_evidence if Path(record["path"]).suffix.lower() in {".png", ".jpg", ".jpeg"}]
    if "image_name" in source:
        name = source["image_name"]
        base.require(base.text(name) and name == Path(name).name and "/" not in name and "\\" not in name,
                     "Capture image_name must be a basename")
        images = [record for record in images if Path(record["path"]).name == name]
    images = _unique(images)
    base.require(len(images) == 1, "Native capture must select one unique PNG/JPEG image")
    base.require(any(Path(record["path"]).suffix.lower() in {".log", ".jsonl"} for record in point_evidence),
                 "Native capture point needs a command log")
    evidence = _unique([dependency, *receipt_evidence, *point_evidence])
    for record in evidence:
        base.check_record(record)
    return {"capture": copy.deepcopy(images[0]), "context": copy.deepcopy(context),
            "evidence": copy.deepcopy(evidence), "dependency_receipt": copy.deepcopy(dependency),
            "task_id": tid, "acceptance_id": aid}


def run_adapter(request_path, result_path):
    request_path, result_path = Path(request_path), Path(result_path)
    request = base.read_json(request_path)
    base.require(isinstance(request, dict) and request.get("format") == FORMAT, "Unsupported visual request format")
    task = request.get("task")
    base.require(isinstance(task, dict) and task.get("kind") == "visual", "Visual adapter requires a visual task")
    base.require(not result_path.exists() and not result_path.is_symlink(), "Visual adapter result must be new")
    acceptance = base.index(request.get("acceptance"), "visual acceptance")
    states = base.index(request.get("states"), "visual states")
    requested = request.get("requested_acceptance_ids")
    base.require(base.ids(requested) and set(requested) <= set(acceptance), "Unknown requested visual acceptance")
    specs = task.get("visual_comparison")
    base.require(isinstance(specs, dict), "Declare task.visual_comparison")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    raw, context = {"points": [], "phase_seconds": {"compare": 0.}}, None
    for aid in requested:
        started = time.monotonic()
        evidence = []
        try:
            spec = specs.get(aid)
            base.require(isinstance(spec, dict), "Missing visual comparison binding: " + aid)
            base.require(set(spec) <= {"assertion_id", "baseline_index", "regions", "capture_from"},
                         "Unknown visual comparison binding fields")
            point = acceptance[aid]
            state_ids = covered_states(point)
            base.require(len(state_ids) == 1 and state_ids[0] in states,
                         "Visual comparison requires one covered state")
            state = states[state_ids[0]]
            assertion_id = spec.get("assertion_id")
            base.require(base.identifier(assertion_id) and any(assertion.get("id") == assertion_id
                         for assertion in point.get("assertions", [])), "Unknown image comparison assertion")
            index = spec.get("baseline_index", 0)
            baselines = state.get("baselines")
            base.require(type(index) is int and isinstance(baselines, list) and 0 <= index < len(baselines),
                         "Visual state has no selected Android baseline")
            baseline = baseline_artifact(baselines[index], request["roots"])
            evidence.append(base.file_record(baseline["path"]))
            native = resolve_capture(request, spec)
            evidence.extend(native["evidence"])
            if context is None:
                context = native["context"]
            base.require(native["context"] == context, "Visual points have different native capture contexts")
            report_dir = result_path.parent / "visual" / aid
            report = compare_images(baseline["path"], native["capture"]["path"], report_dir,
                                    state_id=state["id"], regions=spec.get("regions"))
            comparison = base.file_record(report_dir / "comparison.json")
            evidence.extend([base.file_record(record["path"]) for record in report["inputs"].values()])
            evidence.extend(report["artifacts"].values())
            evidence.append(comparison)
            for record in evidence:
                base.check_record(record)
            raw["points"].append({"acceptance_id": aid, "status": "checked",
                "observations": [{"assertion_id": assertion_id, "actual": report["status"] == "passed"}],
                "visual_comparison": comparison, "evidence": [record["path"] for record in _unique(evidence)]})
        except (ValueError, OSError, KeyError, TypeError) as error:
            # Missing/mismatched evidence is a precondition failure, not a visual product defect.
            usable = []
            for record in evidence:
                try:
                    base.check_record(record)
                    usable.append(record)
                except (ValueError, OSError, KeyError, TypeError):
                    pass
            raw["points"].append({"acceptance_id": aid, "status": "blocked", "reason": str(error),
                                  "evidence": [record["path"] for record in _unique(usable)]})
        finally:
            raw["phase_seconds"]["compare"] += time.monotonic() - started
    if context is not None:
        raw["context"] = context
    runtime.write_json(result_path, raw)
    return raw


if __name__ == "__main__":
    run_adapter(*sys.argv[1:])
