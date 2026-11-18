"""Shared adapter guards and evidence contracts; no business-specific heuristics."""
import hashlib
import copy
import math
import time
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import foundation as base
from foundation import runtime

CATEGORIES = {"precondition_missing", "tool_failure", "product_failure", "environment_changed", "evidence_missing", "unknown"}
RECOVERY = {"precondition_missing": "restore_preconditions", "tool_failure": "repair_and_self_test_tool",
            "product_failure": "reproduce_product_failure", "environment_changed": "restore_checkpoint",
            "evidence_missing": "collect_missing_evidence", "unknown": "inspect_minimal_evidence"}


class ExecutionFailure(ValueError):
    def __init__(self, category, code, step, reason, recovery_point=None):
        super().__init__(reason)
        self.failure = {"category": category, "code": code, "step": step, "reason": reason,
                        "recovery_point": recovery_point}


def failure_record(value):
    base.require(isinstance(value, dict) and value.get("category") in CATEGORIES, "Unknown failure category")
    base.require(all(base.text(value.get(k)) for k in ("code", "step", "reason")), "Failure needs code, step and reason")
    return {**value, "recovery_action": RECOVERY[value["category"]]}


def network_exit_matches(capture, expected):
    """Capture at the final transport boundary, after production interceptors."""
    base.require(isinstance(capture, dict) and isinstance(expected, dict), "Network evidence and expectations must be objects")
    base.require(capture.get("boundary") == "transport", "Network evidence must come from the final transport boundary")
    parsed = urlsplit(capture["url"])
    headers = capture.get("headers")
    # A list preserves duplicate/case-conflicting headers that a dict might hide.
    base.require(isinstance(headers, list) and all(isinstance(h, list) and len(h) == 2 and
                 all(isinstance(v, str) for v in h) for h in headers), "Capture headers as name/value pairs")
    normalized = {}
    for key, value in headers:
        normalized.setdefault(key.lower(), []).append(value)
    url = urlsplit(expected["url"])
    matches = (parsed.scheme, parsed.netloc, parsed.path) == (url.scheme, url.netloc, url.path)
    for key, value in expected.get("headers", {}).items():
        matches = matches and normalized.get(key.lower()) == [value]
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected_query = {**parse_qs(url.query, keep_blank_values=True), **expected.get("query", {})}
    for key, value in expected_query.items():
        matches = matches and query.get(key) == ([value] if isinstance(value, str) else value)
    if "method" in expected:
        matches = matches and capture.get("method") == expected["method"]
    if "body" in expected:
        matches = matches and runtime.digest(capture.get("body")) == runtime.digest(expected["body"])
    return matches


def detector_self_test(detector, samples):
    base.require(isinstance(samples, list) and len(samples) >= 2, "Detector needs positive and negative samples")
    base.require(all(isinstance(s, dict) and type(s.get("expected")) is bool for s in samples),
                 "Detector samples need boolean expectations")
    base.require({s.get("expected") for s in samples} == {True, False}, "Detector needs both expected outcomes")
    results = []
    for sample in samples:
        base.require(base.text(sample.get("role")), "Detector sample needs semantic role")
        actual = detector(sample["input"])
        base.require(type(actual) is bool, "Detector must return a boolean")
        results.append({"role": sample["role"], "expected": sample["expected"], "actual": actual})
    if any(r["actual"] != r["expected"] for r in results):
        raise ExecutionFailure("tool_failure", "detector_self_test", "detector", "Detector failed its positive/negative fixtures")
    return results


class DeviceSuite:
    """One adapter call, fresh semantic targeting before every action and after waits.

    observe() must collect a new layout/screenshot and current device context.
    act() receives the control from that observation, never a cached coordinate.
    """
    def __init__(self, package_sha256, observe, act, *, required_context, max_age_seconds=3,
                 clock=time.time, sleep=time.sleep):
        base.require(base.text(package_sha256) and isinstance(required_context, dict) and required_context,
                     "Suite needs a candidate and current environment conditions")
        base.require(type(max_age_seconds) in (int, float) and math.isfinite(max_age_seconds) and 0 < max_age_seconds <= 3,
                     "Invalid layout age budget")
        self.package = package_sha256
        self.observe, self.act = observe, act
        self.required = {**required_context, "package_sha256": package_sha256}
        self.max_age, self.clock, self.sleep = max_age_seconds, clock, sleep
        self.capture_ids, self.steps, self.phase_seconds = set(), [], {}
        self.action_started = False
        self.action_count = 0
        self.captures, self.evidence, self.context = [], [], None

    def fresh(self, step):
        started = time.monotonic()
        snapshot = copy.deepcopy(self.observe())
        self.phase_seconds["capture"] = self.phase_seconds.get("capture", 0) + time.monotonic() - started
        capture_id, captured = snapshot.get("capture_id"), snapshot.get("captured_at")
        if (not base.text(capture_id) or capture_id in self.capture_ids or type(captured) not in (int, float)
                or not math.isfinite(captured) or not 0 <= self.clock() - captured <= self.max_age):
            raise ExecutionFailure("tool_failure", "stale_layout", step, "Layout must be newly collected before targeting", step)
        self.capture_ids.add(capture_id)
        self.evidence.extend(snapshot.get("evidence", []))
        context = snapshot.get("context", {})
        missing = [k for k, value in self.required.items() if context.get(k) != value]
        if missing:
            category = "environment_changed" if self.context is not None else "precondition_missing"
            raise ExecutionFailure(category, "context_changed", step, "Current context differs: " + ", ".join(missing), step)
        base.require(isinstance(snapshot.get("controls"), list), "Snapshot needs semantic controls")
        self.captures.append({"capture_id": capture_id, "captured_at": captured, "used_at": self.clock(), "context": context})
        self.context = context
        return snapshot

    def action(self, step, selector):
        snapshot = self.fresh(step)
        targets = [c for c in snapshot["controls"] if c.get("id") == selector and c.get("visible") is True
                   and c.get("enabled") is True]
        if (len(targets) != 1 or targets[0].get("effect") not in {"navigation", "read", "selection"}
                or targets[0].get("hit_testable") is not True or targets[0].get("occluded", False)):
            raise ExecutionFailure("precondition_missing", "unsafe_target", step,
                                   "Target is missing, ambiguous or has an unapproved effect", step)
        started = time.monotonic()
        if not 0 <= self.clock() - snapshot["captured_at"] <= self.max_age:
            raise ExecutionFailure("tool_failure", "stale_layout", step, "Layout expired before action", step)
        self.action_started = True
        self.action_count += 1
        self.act(targets[0], snapshot)
        self.phase_seconds["navigate"] = self.phase_seconds.get("navigate", 0) + time.monotonic() - started
        after = self.fresh(step)
        self.steps.append({"id": step, "selector": selector, "before": snapshot["capture_id"],
                           "after": after["capture_id"], "context": after["context"]})
        return after

    def wait(self, step, predicate, *, timeout_seconds=10, interval_seconds=.2):
        base.require(type(timeout_seconds) in (int, float) and math.isfinite(timeout_seconds) and timeout_seconds > 0
                     and type(interval_seconds) in (int, float) and 0 < interval_seconds <= timeout_seconds,
                     "Condition wait needs finite positive budgets")
        started, capture_before = time.monotonic(), self.phase_seconds.get("capture", 0)
        try:
            while True:
                snapshot = self.fresh(step)
                if predicate(snapshot):
                    self.steps.append({"id": step, "capture_id": snapshot["capture_id"], "context": snapshot["context"]})
                    return snapshot
                remaining = timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise ExecutionFailure("precondition_missing", "condition_timeout", step, "Condition wait timed out", step)
                self.sleep(min(interval_seconds, remaining))
        finally:
            # Capture is a child span of wait. Keep phase totals disjoint.
            capture_seconds = self.phase_seconds.get("capture", 0) - capture_before
            self.phase_seconds["wait"] = self.phase_seconds.get("wait", 0) + max(0, time.monotonic() - started - capture_seconds)

    def run(self, points, directory):
        """Run a grouped path and export point results; callbacks supply domain assertions."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        results, failed = [], False
        for point in points:
            aid = point["acceptance_id"]
            base.require(base.identifier(aid), "Invalid suite acceptance ID")
            if failed:
                results.append({"acceptance_id": aid, "status": "not_checked", "reason": "Restore the prior failed checkpoint first"})
                continue
            capture_start, evidence_start = len(self.captures), len(self.evidence)
            try:
                for step in point.get("steps", []):
                    if "selector" in step:
                        self.action(step["id"], step["selector"])
                    else:
                        self.wait(step["id"], step["predicate"], timeout_seconds=step.get("timeout_seconds", 10))
                snapshot = self.fresh(aid)
                started = time.monotonic()
                observations = point["observe"](snapshot)
                self.phase_seconds["assert"] = self.phase_seconds.get("assert", 0) + time.monotonic() - started
                journal = directory / (aid + "-suite.json")
                runtime.write_json(journal, {"package_sha256": self.package, "captures": self.captures[capture_start:]})
                results.append({"acceptance_id": aid, "status": "checked", "observations": observations,
                    "suite_receipt": str(journal.resolve()), "evidence": list(dict.fromkeys(self.evidence[evidence_start:] + [str(journal.resolve())]))})
            except ExecutionFailure as error:
                failed = True
                diagnosis = directory / (aid + "-failure.json")
                runtime.write_json(diagnosis, {"failure": failure_record(error.failure), "completed_steps": self.steps})
                results.append({"acceptance_id": aid, "status": "blocked", "reason": str(error),
                    "failure": failure_record(error.failure), "evidence": list(dict.fromkeys(self.evidence[evidence_start:] + [str(diagnosis.resolve())]))})
        return {"points": results, "context": self.context, "phase_seconds": self.phase_seconds}

    def run_declarative(self, template, request_path, directory, *, resume=False, reconcile=None):
        from suite import run
        return run(self, template, request_path, directory, resume=resume, reconcile=reconcile)


def package_manifest(package_path, modules):
    """Select exact signed outputs supplied by the build adapter, never glob/mtime."""
    target = Path(package_path)
    base.require(not target.exists() and isinstance(modules, list) and modules, "Need a fresh package and explicit modules")
    names = [m["archive_name"] for m in modules]
    base.require(base.ids(names) and all(Path(n).name == n and Path(n).suffix in {".hap", ".hsp"} for n in names),
                 "Manifest needs unique, flat HAP/HSP archive names")
    records = []
    for module in modules:
        record = base.file_record(module["path"])
        base.require(zipfile.is_zipfile(record["path"]), "Module must be a Harmony ZIP")
        signature = base.file_record(module["signature_evidence"])
        proof = base.read_json(signature["path"])
        base.require(proof.get("verified") is True and proof.get("package_sha256") == record["sha256"]
                     and base.text(proof.get("verifier")), "Signature verification must bind the exact module hash")
        records.append({**record, "archive_name": module["archive_name"], "signature_evidence": signature})
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "x", zipfile.ZIP_STORED) as archive:
        for module in records:
            archive.write(module["path"], module["archive_name"])
    return {"package": base.file_record(target), "modules": records}


def validate_build_manifest(raw, evidence):
    manifest = raw.get("output_manifest")
    base.require(isinstance(manifest, dict) and manifest.get("modules"), "Build needs an explicit output_manifest")
    base.check_record(manifest["package"])
    base.require(manifest["package"]["path"] == str(Path(raw["package_path"]).resolve()), "Manifest package binding differs")
    known = {e["path"]: e for e in evidence}
    names = [m["archive_name"] for m in manifest["modules"]]
    base.require(base.ids(names), "Duplicate build module")
    base.require(all(Path(n).name == n and Path(n).suffix in {".hap", ".hsp"} for n in names), "Invalid module archive name")
    base.require(zipfile.is_zipfile(raw["package_path"]), "Manifest candidate must be a ZIP package")
    with zipfile.ZipFile(raw["package_path"]) as archive:
        base.require(sorted(archive.namelist()) == sorted(names), "Package members differ from manifest")
        for module in manifest["modules"]:
            record = {k: module[k] for k in ("path", "sha256")}
            base.check_record(record)
            base.require(known.get(record["path"]) == record, "Module must appear in build evidence")
            signature = module["signature_evidence"]
            base.check_record(signature)
            base.require(known.get(signature["path"]) == signature, "Signature proof must appear in build evidence")
            proof = base.read_json(signature["path"])
            base.require(proof.get("verified") is True and proof.get("package_sha256") == record["sha256"]
                         and base.text(proof.get("verifier")), "Invalid module signature proof")
            base.require(hashlib.sha256(archive.read(module["archive_name"])).hexdigest() == record["sha256"],
                         "Packaged module hash differs")
    return manifest


def validate_policy(task, raw, point, records, roots):
    policy = task.get("execution_policy", {})
    aid = point["acceptance_id"]
    matches = True
    if aid in policy.get("network_exit", {}):
        capture_path = point.get("network_capture")
        base.require(any(r["path"] == capture_path for r in records), "Network capture must belong to point evidence")
        matches = network_exit_matches(base.read_json(capture_path), policy["network_exit"][aid])
    if aid in policy.get("detectors", {}):
        definition = policy["detectors"][aid]
        rule_ref = definition["rule"]
        rule = base.read_json(base.relative_file(Path(roots[rule_ref["root"]]), rule_ref["path"]))
        path = point.get("layout_capture")
        base.require(any(r["path"] == path for r in records), "Detector needs a current layout capture in point evidence")
        layout = base.read_json(path)
        base.require(any(r["sha256"] == layout.get("image_sha256") and Path(r["path"]).suffix.lower() in {".png", ".jpg", ".jpeg"}
                         for r in records), "Detector layout must bind this point's screenshot")
        matches = matches and layout_matches(layout, rule)
    return matches


def policy_references(task):
    for detector in task.get("execution_policy", {}).get("detectors", {}).values():
        yield detector["rule"]
        if detector.get("baseline"):
            yield detector["baseline"]["image"]
            yield detector["baseline"]["layout"]
        for sample in detector["samples"]:
            yield sample["input"]


def layout_matches(layout, rule):
    controls = {c["id"] for c in layout.get("controls", []) if c.get("visible") is True}
    return (set(rule["required_controls"]) <= controls and not set(rule.get("forbidden_controls", [])) & controls
            and all(layout.get("context", {}).get(k) == v for k, v in rule.get("context", {}).items()))


def check_detectors(task, roots):
    records = []
    for detector in task.get("execution_policy", {}).get("detectors", {}).values():
        def load(ref):
            base.require(ref["root"] in roots, "Unknown detector root")
            record = base.file_record(base.relative_file(Path(roots[ref["root"]]), ref["path"]))
            records.append(record)
            return base.read_json(record["path"])
        rule = load(detector["rule"])
        base.require(base.ids(rule.get("required_controls")) and rule["required_controls"],
                     "Detector must require semantic controls")
        samples = [{**s, "input": load(s["input"])} for s in detector["samples"]]
        detector_self_test(lambda layout: layout_matches(layout, rule), samples)
        if detector.get("baseline"):
            layout = load(detector["baseline"]["layout"])
            ref = detector["baseline"]["image"]
            image = base.file_record(base.relative_file(Path(roots[ref["root"]]), ref["path"]))
            records.append(image)
            if layout.get("image_sha256") != image["sha256"] or not layout_matches(layout, rule):
                raise ExecutionFailure("tool_failure", "wrong_baseline_state", "baseline",
                                       "Baseline image/layout does not match the declared semantic state")
    return records


def validate_execution_policy(task):
    policy = task.get("execution_policy", {})
    base.require(isinstance(policy, dict) and set(policy) <= {"build_manifest", "network_exit", "detectors", "device_guard"},
                 "Unknown execution policy")
    if "build_manifest" in policy:
        base.require(task["kind"] == "build" and policy["build_manifest"] is True, "Build manifest policy is build-only")
    if "device_guard" in policy:
        base.require(task["kind"] in {"device", "visual"} and isinstance(policy["device_guard"], dict)
                     and policy["device_guard"], "Device guard needs explicit current conditions")
    for key in ("network_exit", "detectors"):
        base.require(isinstance(policy.get(key, {}), dict) and set(policy.get(key, {})) <= set(task["acceptance_ids"]),
                     "Execution policy must name owned acceptance points")
    for expected in policy.get("network_exit", {}).values():
        base.require(isinstance(expected, dict) and base.text(expected.get("url")), "Network policy needs expected URL")
        base.require(urlsplit(expected["url"]).scheme in {"http", "https"}, "Network policy needs an HTTP URL")
        base.require(all(isinstance(expected.get(key, {}), dict) for key in ("headers", "query")),
                     "Network headers/query expectations must be objects")
    for detector in policy.get("detectors", {}).values():
        base.require(isinstance(detector, dict) and isinstance(detector.get("samples"), list), "Detector needs sample fixtures")
        base.require(all(isinstance(s, dict) and type(s.get("expected")) is bool for s in detector["samples"]), "Detector expectations must be boolean")
        base.require({s["expected"] for s in detector["samples"]} == {True, False}, "Detector needs positive and negative examples")


def validate_device_guard(task, raw, point, records):
    expected = task.get("execution_policy", {}).get("device_guard")
    if not expected:
        return
    path = point.get("suite_receipt", raw.get("suite_receipt"))
    base.require(any(r["path"] == path for r in records), "Guarded suite receipt must belong to point evidence")
    journal = base.read_json(path)
    context = raw["context"]
    base.require(journal.get("package_sha256") == context["package_sha256"], "Suite candidate changed")
    captures, seen = journal.get("captures"), set()
    base.require(isinstance(captures, list) and captures, "Guarded suite needs captures")
    for capture in captures:
        base.require(base.text(capture.get("capture_id")) and capture["capture_id"] not in seen,
                     "Suite reused a layout capture")
        seen.add(capture["capture_id"])
        captured, used = capture.get("captured_at"), capture.get("used_at")
        base.require(type(captured) in (int, float) and type(used) in (int, float)
                     and math.isfinite(captured) and math.isfinite(used) and 0 <= used - captured <= 3,
                     "Suite used an expired layout")
        for key, value in {**expected, "package_sha256": context["package_sha256"]}.items():
            base.require(capture.get("context", {}).get(key) == value, "Suite environment changed: " + key)


def validate_execution_window(task, raw, request, process):
    """Receipt freshness is relative to this attempt, not just to its own timestamps."""
    policy = task.get("execution_policy", {})
    if not policy.get("device_guard") and not policy.get("network_exit") and not task.get("device_suite"):
        return
    start, end = process["started_at"], process["finished_at"]
    for point in raw.get("points", []):
        if point["status"] != "checked":
            continue
        aid = point["acceptance_id"]
        captures = []
        if policy.get("device_guard") or task.get("device_suite"):
            journal = base.read_json(point.get("suite_receipt", raw.get("suite_receipt")))
            captures += journal["captures"]
        if task.get("device_suite"):
            binding = base.read_json(point["suite_binding"])
            base.check_record(binding["request"])
            base.require(base.read_json(binding["request"]["path"]) == request, "Declared suite belongs to another request")
        if aid in policy.get("network_exit", {}):
            capture = base.read_json(point["network_capture"])
            if capture.get("input_digest") != request["input_digest"]:
                raise ExecutionFailure("evidence_missing", "stale_execution_evidence", aid,
                                       "Transport capture belongs to different inputs", aid)
            captures.append(capture)
        for capture in captures:
            captured = capture.get("captured_at")
            used = capture.get("used_at", captured)
            if (type(captured) not in (int, float) or type(used) not in (int, float)
                    or not math.isfinite(captured) or not math.isfinite(used)
                    or not start <= captured <= used <= end):
                raise ExecutionFailure("evidence_missing", "stale_execution_evidence", aid,
                                       "Capture must belong to the current executor time window", aid)
