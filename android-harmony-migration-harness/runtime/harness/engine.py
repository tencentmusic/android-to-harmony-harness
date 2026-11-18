"""Reviewed migration: human gates and state-level results with supervised execution."""
from __future__ import annotations

import copy
import math
import os
import shutil
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

import foundation as base
from foundation import runtime
from contract import FORMAT, AGENT_PHASES, artifact, baseline_artifact, material_bundle, profile_data, timings, validate_contract
from test_plan import covered_states, references as plan_references, scope_plan, summary as plan_summary, validate_revision
from project import readiness as project_readiness
from execution import (ExecutionFailure, failure_record, validate_policy, validate_device_guard,
                       validate_build_manifest, check_detectors, validate_execution_window)

STATE = base.STATE
AGENT_KINDS = base.AGENT_KINDS
ancestors, input_files, relative_file = base.ancestors, base.input_files, base.relative_file
inside = base.inside
identifier = base.identifier
read_json, require, text, ids = base.read_json, base.require, base.text, base.ids
file_record, check_record = base.file_record, base.check_record


def screenshot(record):
    path = Path(record["path"])
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        return False
    with path.open("rb") as stream:
        header = stream.read(8)
    require(header == b"\x89PNG\r\n\x1a\n" or header.startswith(b"\xff\xd8\xff"), "Invalid screenshot signature")
    base.validate_image(record)
    return True


class Harness:
    def project_profile(self):
        context = getattr(self, "_query_context", None)
        if context is None:
            return profile_data(self.contract)
        if "profile" not in context:
            context["profile"] = profile_data(self.contract)
        return context["profile"]

    def save(self, event, **details):
        self.state["events"].append({"sequence": len(self.state["events"]) + 1,
                                     "time": time.time(), "kind": event, **details})
        runtime.write_json(self.root / STATE, self.state)
        from session import checkpoint
        try:
            checkpoint(self, event)
        except (OSError, ValueError, KeyError, TypeError):
            # Optional host telemetry must not turn a committed task into failure.
            # Recovery reports a stale/corrupt checkpoint; end-check stays closed.
            pass

    def __init__(self, run_dir):
        self.root = Path(run_dir).resolve(strict=True)
        if not (self.root / STATE).is_file() and any((self.root / name).is_file() for name in
                ("experiment-state.json", "manifest.json", "verification-report.json")):
            raise ValueError("Unsupported historical run; preserve its records and use start with a migration-harness/1 "
                             "contract in a fresh directory. Standalone capture-screen/compare-visual need no run.")
        self.state = read_json(self.root / STATE)
        require(self.state.get("format") == FORMAT, "Unsupported run format; use the current Harness contract")
        require(not any((self.root / f).exists() for f in ("manifest.json", "verification-report.json")),
                "Run directory contains unsupported historical records; preserve them and use a fresh directory")
        check_record(self.state["contract"])
        # Stored contracts were validated at init/revise. Live resources may now be
        # missing; loading them here would also disable diagnosis and repair.
        self.contract = read_json(self.state["contract"]["path"])
        require(self.contract.get("format") == FORMAT, "Unsupported Harness contract")
        self.tasks = {t["id"]: t for t in self.contract["tasks"]}
        self.caps = {c["id"]: c for c in self.contract["capabilities"]}
        self.acceptance = {a["id"]: a for a in self.contract["acceptance"]}
        self.states = {s["id"]: s for s in self.contract["states"]}
        self.scopes = {s["id"]: s for s in self.contract["review_scopes"]}

    @classmethod
    def init(cls, contract_path, run_dir):
        path = Path(contract_path).resolve(strict=True)
        contract = validate_contract(read_json(path), path.parent)
        root = Path(run_dir).resolve()
        require(not root.exists(), "Use a fresh run directory")
        require(not any(base.inside(root, Path(p)) or base.inside(Path(p), root)
                        for p in contract["roots"].values()), "Run and source roots must be disjoint")
        root.mkdir(parents=True)
        target = root / "contracts/001.json"
        runtime.write_json(target, contract)
        runtime.write_json(root / STATE, {"format": FORMAT, "run_id": uuid.uuid4().hex,
            "contract": file_record(target), "revision": 1, "created_at": time.time(),
            "tasks": {}, "operations": {}, "events": [], "joint_reviews": {}, "timers": {}})
        return cls(root)

    def scope_snapshot(self, cid):
        scope = self.scopes[cid]
        states = [s for s in self.states.values() if s["capability_id"] == cid]
        refs = [scope[d] for d in ("requirements", "technical", "ui")]
        refs += [{"root": "android", **source} for source in self.caps[cid]["android_sources"]]
        artifacts = [artifact(r, self.contract["roots"]) for r in refs]
        for state in states:
            artifacts.extend(artifact(r, self.contract["roots"]) for r in state["sources"])
            artifacts.extend(baseline_artifact(r, self.contract["roots"]) for r in state["baselines"])
        profile, profile_records = self.project_profile()
        plan = scope_plan(self.contract.get("test_plan"), {cid})
        if plan is not None:
            artifacts.extend(artifact(r, self.contract["roots"]) for r in plan_references(plan))
        policies = {t["id"]: t["execution_policy"] for t in self.tasks.values()
                    if cid in t["capability_ids"] and t.get("execution_policy")}
        presets = {t["id"]: t["execution_presets"] for t in self.tasks.values()
                   if cid in t["capability_ids"] and t.get("execution_presets")}
        suites = {t["id"]: artifact(t["device_suite"], self.contract["roots"]) for t in self.tasks.values()
                  if cid in t["capability_ids"] and t.get("device_suite")}
        visual = {t["id"]: t["visual_comparison"] for t in self.tasks.values()
                  if cid in t["capability_ids"] and "visual_comparison" in t}
        return {"capability": self.caps[cid], "scope": scope, "states": states,
                **({"execution_policies": policies} if policies else {}),
                **({"execution_presets": presets} if presets else {}),
                **({"device_suites": suites} if suites else {}),
                **({"visual_comparisons": visual} if visual else {}),
                **({"test_plan": plan} if plan is not None else {}),
                "point_reuse_policy": {t["id"]: t.get("independent_points", False)
                                       for t in self.tasks.values() if cid in t["capability_ids"] and t["kind"] in base.TEST_KINDS},
                "material_bundles": [material_bundle(scope[d], self.contract["roots"])
                                     for d in ("requirements", "technical", "ui")],
                "acceptance": [a for a in self.acceptance.values() if a["capability_id"] == cid],
                "artifacts": artifacts,
                "project": profile, "project_evidence": profile_records}

    def joint_status(self, cid):
        context = getattr(self, "_query_context", None)
        if context is not None and cid in context.get("gates", {}):
            return context["gates"][cid]
        result = self._joint_status(cid)
        if context is not None:
            context.setdefault("gates", {})[cid] = result
        return result

    def _joint_status(self, cid):
        result = {"capability_id": cid, "status": "awaiting_human"}
        try:
            result["input_digest"] = runtime.digest(self.scope_snapshot(cid))
            record = self.state["joint_reviews"].get(cid)
            if record:
                check_record(record)
                approval = read_json(record["path"])
                require(approval["input_digest"] == result["input_digest"], "Review materials changed")
                self.check_review_archive(approval["snapshot"])
                for evidence in approval["evidence"]:
                    check_record(evidence)
                result.update(status="approved" if all(v == "approved" for v in approval["decisions"].values())
                              else "rejected", reviewer=approval["reviewer"], decisions=approval["decisions"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            result.update(status="stale", reason=str(error))
        return result

    def input_snapshot(self, task):
        from requirements import policy_gaps
        require(not policy_gaps(task), "Execution policies need configuration: " + ", ".join(policy_gaps(task)))
        snapshot = self._source_snapshot(task)
        snapshot["format"] = FORMAT
        snapshot["engine_files"] = [file_record(Path(__file__).with_name(name))
                                 for name in ("engine.py", "contract.py", "foundation.py")]
        plan = scope_plan(self.contract.get("test_plan"), set(task["capability_ids"]))
        if plan is not None:
            snapshot["test_plan"] = plan
            snapshot["test_plan_evidence"] = [artifact(r, self.contract["roots"]) for r in plan_references(plan)]
            snapshot["engine_files"].append(file_record(Path(__file__).with_name("test_plan.py")))
        profile, records = self.project_profile()
        snapshot["project"] = {"profile": profile, "evidence": records}
        if task.get("execution_presets"):
            snapshot["engine_files"].append(file_record(Path(__file__).with_name("requirements.py")))
        if "visual_comparison" in task:
            snapshot["engine_files"].extend(file_record(Path(__file__).with_name(name)) for name in
                                          ("visual.py", "visual_adapter.py", "visual_binding.py"))
        if task.get("device_suite"):
            from suite import for_task
            record = artifact(task["device_suite"], self.contract["roots"])
            for_task(read_json(record["path"]), task, self.acceptance)
            snapshot["device_suite"] = record
            snapshot["engine_files"].extend(file_record(Path(__file__).with_name(name)) for name in ("suite.py", "execution.py"))
        if task.get("execution_policy"):
            snapshot["execution_policy_evidence"] = check_detectors(task, self.contract["roots"])
            snapshot["engine_files"].append(file_record(Path(__file__).with_name("execution.py")))
        if task.get("project_capability_ids"):
            snapshot["engine_files"].append(file_record(Path(__file__).with_name("project.py")))
            configured = project_readiness(profile, self.contract["roots"], records[0]["path"],
                                           self.contract.get("demo", False), task["project_capability_ids"])
            require(configured["status"] == "verified", "Project capabilities need verification: " +
                    ", ".join(r["id"] for r in configured["capabilities"] if r["status"] != "verified"))
            snapshot["project_execution"] = {r["id"]: {"input_digest": r["input_digest"], "command": r["command"]}
                                             for r in configured["capabilities"]}
            if task.get("project_command"):
                require(task["command"] == snapshot["project_execution"][task["project_command"]]["command"],
                        "Project command changed; revise the task binding")
        state_ids = {sid for a in task["acceptance_ids"] for sid in covered_states(self.acceptance[a])}
        if task["kind"] == "discover":
            state_ids.update(sid for sid, state in self.states.items() if state["capability_id"] in task["capability_ids"])
        snapshot["states"] = [self.states[s] for s in sorted(state_ids - {None})]
        snapshot["state_evidence"] = []
        for state in snapshot["states"]:
            snapshot["state_evidence"].extend(artifact(r, self.contract["roots"]) for r in state["sources"])
            snapshot["state_evidence"].extend(baseline_artifact(r, self.contract["roots"]) for r in state["baselines"])
        if task["kind"] == "implement":
            snapshot["joint_reviews"] = {}
            for cid in task["capability_ids"]:
                require(self.joint_status(cid)["status"] == "approved", "Joint review required: " + cid)
                snapshot["joint_reviews"][cid] = self.state["joint_reviews"][cid]
        return snapshot

    def final_snapshot(self):
        return {"contract": self.state["contract"], "joint_reviews": self.state["joint_reviews"],
                "receipts": {tid: self.state["tasks"].get(tid, {}).get("receipt") for tid in self.tasks}}

    def final_status(self, ready):
        result = {"status": "awaiting_human" if ready else "blocked"}
        if not ready:
            return result
        result["input_digest"] = runtime.digest(self.final_snapshot())
        record = self.state.get("final_approval")
        if record:
            try:
                check_record(record)
                approval = read_json(record["path"])
                require(approval["input_digest"] == result["input_digest"], "Final evidence changed")
                for evidence in approval["evidence"]:
                    check_record(evidence)
                result.update(status=approval["decision"], reviewer=approval["reviewer"])
            except (OSError, ValueError, KeyError, TypeError) as error:
                result.update(status="stale", reason=str(error))
        return result

    def status(self):
        # Share explicit profile/gate reads only within this read-only query.
        # Mutations and later queries always collect new content hashes.
        previous = getattr(self, "_query_context", None)
        self._query_context = {}
        try:
            return self._status()
        finally:
            self._query_context = previous

    def _status(self):
        rows, valid_results = {}, {}
        gates = {cid: self.joint_status(cid) for cid in self.caps}
        for tid in base.order_tasks(self.tasks):
            task, saved = self.tasks[tid], self.state["tasks"].get(tid, {})
            row = {"id": tid, "kind": task["kind"], "status": "ready", "attempts": saved.get("attempts", 0)}
            if saved.get("active_problem_ids"):
                row["problems"] = [self.state["problems"][key] for key in saved["active_problem_ids"]]
            rows[tid] = row
            if saved.get("running"):
                row.update(status="in_doubt", reason="Reconcile interrupted execution before retry")
                continue
            blockers = [t for t in task["depends_on"] if rows[t]["status"] != "passed"]
            if blockers:
                row.update(status="blocked", dependencies=blockers)
                continue
            if task["kind"] == "implement":
                blocked = [cid for cid in task["capability_ids"] if gates[cid]["status"] != "approved"]
                if blocked:
                    row.update(status="awaiting_human", review_scopes=blocked)
                    continue
            try:
                from visual_binding import require_real_binding
                require_real_binding(self.contract, task)
                row["input_digest"] = self.fingerprint(task)
                if task.get("independent_points"):
                    valid_results[tid] = [p["result"] for p in self.current_points(task, row["input_digest"]).values()]
                if saved.get("receipt"):
                    receipt = self.receipt(tid)
                    require(receipt["input_digest"] == row["input_digest"], "Inputs/dependencies changed")
                    for evidence in receipt["evidence"]:
                        check_record(evidence)
                    if "point_results" in receipt and not task.get("independent_points"):
                        valid_results[tid] = receipt["point_results"]
                    points_valid = (not task.get("independent_points") or
                                    len(valid_results.get(tid, [])) == len(task["acceptance_ids"]) and
                                    all(p["status"] == "passed" for p in valid_results[tid]))
                    if receipt.get("accepted", True) and points_valid:
                        row["status"] = "passed"
                        continue
            except (OSError, ValueError, KeyError, TypeError) as error:
                row["reason"] = str(error)
                if isinstance(error, ExecutionFailure):
                    row["failure"] = failure_record(error.failure)
                if "input_digest" not in row:
                    row["status"] = "input_missing"
                    continue
                row["status"] = "stale"
            budget = self.failure_budget(task, row["input_digest"])
            row.update(budget)
            recurrent = [p for p in row.get("problems", []) if p["occurrences"] >= 3 and (
                not p["diagnoses"] or p["diagnoses"][-1].get("after_occurrence", 0) < p["occurrences"])]
            if budget["failed_attempts"] >= budget["failure_limit"]:
                row.update(status="budget_exhausted", reason="Failure budget exhausted for these inputs")
            elif saved.get("failure_digest") == row["input_digest"] and not saved.get("retry_allowed"):
                row.update(status="needs_diagnosis", reason=saved.get("error", "Required points did not pass"))
            elif recurrent:
                row.update(status="needs_diagnosis", reason="Recurring problem needs diagnosis even when inputs or operation IDs change")
            elif task["kind"] in base.AGENT_KINDS:
                row["status"] = "agent_required"
        points = []
        for tid, task in self.tasks.items():
            known = {p["acceptance_id"]: p for p in valid_results.get(tid, [])}
            for aid in task["acceptance_ids"]:
                item = self.acceptance[aid]
                result = known.get(aid, {"status": "blocked" if rows[tid]["status"] in
                    {"blocked", "awaiting_human", "input_missing", "in_doubt"} else "not_checked",
                    "reason": rows[tid].get("reason", rows[tid]["status"])})
                points.append({"acceptance_id": aid, "state_id": item.get("state_id"),
                               **({"covered_state_ids": covered_states(item)} if "test_plan" in self.contract else {}),
                               "kind": item["kind"], "task_id": tid, **result})
        counts = {s: sum(p["status"] == s for p in points) for s in ("passed", "failed", "not_checked", "blocked")}
        all_passed = all(r["status"] == "passed" for r in rows.values()) and counts["passed"] == len(points)
        final = self.final_status(all_passed)
        complete = all_passed and final["status"] == "approved"
        return {"format": FORMAT, "feature_id": self.contract["feature_id"],
                "status": ("demo_only" if self.contract.get("demo") else "delivery_ready") if complete else "incomplete",
                "joint_review": list(gates.values()), "final_review": final,
                "coverage": {**counts, "required": len(points), "unit": "acceptance_point"},
                "state_coverage": [{"state_id": sid, "kind": s["kind"], "expected": s["expected"],
                    "baseline_gap": s.get("baseline_gap"),
                    "points": [{"id": p["acceptance_id"], "kind": p["kind"], "status": p["status"]}
                               for p in points if sid in covered_states(self.acceptance[p["acceptance_id"]])],
                    **({"selection": "excluded" if any(x["state_id"] == sid for x in self.contract["test_plan"].get("excluded_states", []))
                                      else "selected"} if "test_plan" in self.contract else {})} for sid, s in self.states.items()],
                "points": points, "covered_acceptance_ids": sorted(p["acceptance_id"] for p in points if p["status"] == "passed"),
                "ready_tasks": [tid for tid, r in rows.items() if r["status"] in {"ready", "stale"}],
                "agent_tasks": [tid for tid, r in rows.items() if r["status"] == "agent_required"],
                "tasks": list(rows.values()), "metrics": self.metrics(),
                **({"test_plan": plan_summary(self.contract, points)} if "test_plan" in self.contract else {})}

    def failure_budget(self, task, fingerprint):
        saved = self.state["tasks"].get(task["id"], {})
        failures = sum(v == fingerprint for v in saved.get("failed_attempts", {}).values())
        grants = sum(g["additional"] for g in saved.get("budget_grants", []) if g["input_digest"] == fingerprint)
        return {"failed_attempts": failures, "failure_limit": task.get("max_attempts", 5) + grants}

    def note_failure(self, task, fingerprint, attempt_dir, failures=None):
        saved = self.state["tasks"].setdefault(task["id"], {})
        saved.setdefault("failed_attempts", {})[attempt_dir.name] = fingerprint
        active = []
        for failure in failures or [{"category": "unknown", "code": "execution_failed", "step": task["id"],
                                      "reason": "Inspect attempt result and process log"}]:
            failure = failure_record(failure)
            key = runtime.digest([sorted(task["capability_ids"]), task["kind"], failure["category"], failure["code"], failure["step"]])
            problem = self.state.setdefault("problems", {}).setdefault(key, {"id": key, **failure, "attempts": [], "diagnoses": []})
            if not any(a["directory"] == str(attempt_dir) for a in problem["attempts"]):
                problem["attempts"].append({"directory": str(attempt_dir), "input_digest": fingerprint,
                                            "task_id": task["id"], "recorded_at": time.time()})
            problem.update(reason=failure["reason"], recovery_point=failure.get("recovery_point"),
                           occurrences=len(problem["attempts"]), status="needs_diagnosis")
            active.append(key)
        saved["active_problem_ids"] = active
        saved["problem_history_ids"] = sorted(set(saved.get("problem_history_ids", []) + active))

    def current_points(self, task, fingerprint):
        saved = self.state["tasks"].get(task["id"], {})
        points = {}
        for aid, record in saved.get("point_receipts", {}).items():
            try:
                check_record(record)
                point = read_json(record["path"])
                require(aid in task["acceptance_ids"] and point["input_digest"] == fingerprint,
                        "Point inputs changed")
                require(point["context"] == saved.get("point_context"), "Point environment changed")
                for evidence in point["evidence"]:
                    check_record(evidence)
                points[aid] = point
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return points

    def extend_budget(self, operation_id, task_id, additional, reason, evidence_paths):
        require(type(additional) is int and 1 <= additional <= 20, "Budget grant must be 1..20")
        with self.locked():
            require(task_id in self.tasks and text(reason), "Budget grant needs task and reason")
            evidence = self.evidence(evidence_paths)
            request = {"command": "extend-budget", "task_id": task_id, "additional": additional,
                       "reason": reason, "evidence": evidence}
            if operation_id in self.state["operations"]:
                return self.replay(operation_id, request)
            row = next(r for r in self.status()["tasks"] if r["id"] == task_id)
            require(row["status"] == "budget_exhausted", "Only an exhausted failure budget can be extended")
            self.replay(operation_id, request)
            saved = self.state["tasks"][task_id]
            saved.setdefault("budget_grants", []).append({**request, "input_digest": row["input_digest"]})
            self.save("budget_extended", task_id=task_id, additional=additional, reason=reason, evidence=evidence)
            self.done(operation_id)
            return self.status()

    def metrics(self):
        from session import metrics as session_metrics
        by_kind, phases, costs, executions = {}, {}, {}, []
        for tid, saved in self.state["tasks"].items():
            task = self.tasks.get(tid) or self.state.get("retired_tasks", {})[tid]
            bucket = by_kind.setdefault(task["kind"], {"attempts": 0, "seconds": 0})
            bucket["attempts"] += saved.get("attempts", 0)
            bucket["seconds"] += saved.get("execution_seconds", 0)
            for phase, seconds in saved.get("phase_seconds", {}).items():
                phases[phase] = round(phases.get(phase, 0) + seconds, 6)
            for directory, process in saved.get("execution_receipts", {}).items():
                executions.append({"task_id": tid, "directory": directory, **process})
                category = process["cost_category"]
                costs[category] = costs.get(category, 0) + process["seconds"]
        for bucket in by_kind.values():
            bucket["seconds"] = round(bucket["seconds"], 6)
        spans = []
        now = time.time()
        for span_id, span in self.state["timers"].items():
            spans.append({"id": span_id, **span, "elapsed_seconds": round(max(0, span.get("ended_at", now) - span["started_at"]), 3)})
        intervals = sorted((s["started_at"], s.get("ended_at", now)) for s in self.state["timers"].values())
        merged = []
        for start, end in intervals:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        setup = list(self.state.get("project_setup_receipts", {}).values())
        return {"executor_attempts": sum(v["attempts"] for v in by_kind.values()),
                "executor_seconds": round(sum(v["seconds"] for v in by_kind.values()), 6),
                "executor_by_kind": by_kind, "adapter_reported_phase_seconds": phases,
                "agent_wall_spans": spans, "wall_seconds_since_init": round(max(0, now-self.state["created_at"]), 3),
                "agent_wall_union_seconds": round(sum(max(0, end-start) for start, end in merged), 3),
                "execution_receipts": executions, "executor_cost_seconds": {k: round(v, 6) for k, v in costs.items()},
                "reused_project_setup_receipts": setup, "session": session_metrics(self.root),
                "problems": list(self.state.get("problems", {}).values()), "unrecorded_external_seconds": None,
                "note": "Executor time includes waits; agent spans may overlap execution. Project setup is historical, not task execution. External unrecorded work and model/token time remain unknown."}

    def project_context(self):
        profile, records = profile_data(self.contract)
        tools = []
        for tool in profile["tools"]:
            executable = tool["argv"][0]
            resolved = shutil.which(executable)
            tools.append({**tool, "executable": resolved, "available": resolved is not None})
        return {"project": profile["name"], "roots": self.contract["roots"], "modules": profile["modules"],
                "knowledge": records, "tools": tools,
                "project_readiness": project_readiness(profile, self.contract["roots"], records[0]["path"],
                                                       self.contract.get("demo", False)),
                "capabilities": list(self.caps.values()), "states": list(self.states.values()),
                "guidance": "Read project knowledge first, then targeted source searches. Tools are inventoried, not auto-executed."}

    def review_packet(self):
        status = self.status()
        scopes = []
        for gate in status["joint_review"]:
            try:
                scopes.append({**gate, **self.scope_snapshot(gate["capability_id"])})
            except (OSError, ValueError, KeyError, TypeError) as error:
                scopes.append({**gate, "reason": str(error)})
        return {"format": FORMAT, "feature_id": self.contract["feature_id"],
                "joint_review": scopes,
                "baseline_gaps": [{"state_id": sid, "capability_id": s["capability_id"],
                                   "reason": s["baseline_gap"]} for sid, s in self.states.items()
                                  if not s["baselines"] and ("test_plan" not in self.contract or
                                      any(a["kind"] == "visual" and sid in covered_states(a) for a in self.acceptance.values()))],
                "final_review": status["final_review"], "points": status["points"],
                **({"test_plan": status["test_plan"]} if "test_plan" in status else {}),
                "authority": "Human decisions must refer to the displayed digest and actual reviewed artifacts."}

    def archive_review(self, cid, directory, expected_digest):
        snapshot = self.scope_snapshot(cid)
        require(runtime.digest(snapshot) == expected_digest, "Review materials changed before archiving")
        records = {}

        def collect(value):
            if isinstance(value, dict):
                if "path" in value and "sha256" in value:
                    records[value["path"]] = value["sha256"]
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(snapshot)
        archived = []
        for source, sha in sorted(records.items()):
            origin = Path(source)
            source_root = next(name for name, root in self.contract["roots"].items()
                               if base.inside(origin, Path(root)))
            target = directory / "files" / source_root / origin.relative_to(self.contract["roots"][source_root])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            record = base.file_record(target, allow_empty=True)
            require(record["sha256"] == sha, "Review material changed during archiving")
            archived.append({"source": source, "archived": record})
        require(runtime.digest(self.scope_snapshot(cid)) == expected_digest, "Review materials changed during archiving")
        target = directory / "snapshot.json"
        runtime.write_json(target, {"snapshot": snapshot, "files": archived})
        return file_record(target)

    def check_review_archive(self, record):
        check_record(record)
        for item in read_json(record["path"])["files"]:
            archived = item["archived"]
            require(base.file_record(archived["path"], allow_empty=True) == archived, "Review archive changed")

    def approve(self, operation_id, result):
        with self.locked():
            if operation_id in self.state["operations"]:
                return self.replay(operation_id, {"command": "approve", "result": result})
            require(result.get("actor_type") == ("demo" if self.contract.get("demo") else "human"),
                    "Approval must identify a human; synthetic approval is allowed only in demo mode")
            require(text(result.get("reviewer")) and text(result.get("summary")), "Approval needs reviewer and conclusion")
            evidence = self.evidence(result.get("evidence"))
            status = self.status()
            kind = result.get("gate")
            entries = []
            if kind == "joint":
                reviews = result.get("reviews")
                require(isinstance(reviews, list) and reviews, "Joint approval needs scope decisions")
                require(all(isinstance(r, dict) for r in reviews), "Each review must be an object")
                require(ids([r.get("capability_id") for r in reviews]), "Duplicate review scope")
                rows = {r["id"]: r for r in status["tasks"]}
                for review in reviews:
                    cid = review.get("capability_id")
                    require(cid in self.caps, "Unknown review scope")
                    require(all(rows[t["id"]]["status"] == "passed" for t in self.tasks.values()
                                if t["kind"] == "discover" and cid in t["capability_ids"]),
                            "Complete discovery before joint review")
                    current = self.joint_status(cid)
                    require(text(current.get("input_digest")) and review.get("input_digest") == current["input_digest"],
                            "Stale joint review digest")
                    decisions = review.get("decisions")
                    require(isinstance(decisions, dict) and set(decisions) == {"requirements", "technical", "ui"}
                            and all(d in {"approved", "rejected"} for d in decisions.values()),
                            "Requirements, technical and UI need separate decisions")
                    entries.append((cid, {**review, "reviewer": result["reviewer"], "evidence": evidence,
                                         "actor_type": result["actor_type"], "summary": result["summary"]}))
            else:
                require(kind == "final", "Unknown approval gate")
                final = status["final_review"]
                require(final["status"] != "blocked", "Every required point and agent review must pass before final approval")
                require(result.get("input_digest") == final.get("input_digest"), "Stale final review digest")
                require(result.get("decision") in {"approved", "rejected"}, "Final decision required")
                entries.append(("final", {**result, "evidence": evidence}))
            self.replay(operation_id, {"command": "approve", "result": result})
            directory = self.attempt_dir("human-" + kind)
            for cid, entry in entries:
                if kind == "joint":
                    entry["snapshot"] = self.archive_review(cid, directory / cid, entry["input_digest"])
                target = directory / (cid + ".json")
                runtime.write_json(target, {**entry, "recorded_at": time.time()})
                if kind == "joint":
                    self.state["joint_reviews"][cid] = file_record(target)
                else:
                    self.state["final_approval"] = file_record(target)
            self.save("human_review_recorded", gate=kind, reviewer=result["reviewer"])
            for span_id, span in self.state["timers"].items():
                if span["phase"] != "review_wait" or "ended_at" in span:
                    continue
                if span.get("gate", kind) != kind:
                    continue
                pending = span.get("review_inputs", {}).copy()
                for cid, entry in entries:
                    if pending.get(cid) == entry["input_digest"]:
                        pending.pop(cid)
                if span.get("review_inputs"):
                    span["review_inputs"] = pending
                    if pending:
                        continue
                span.update(ended_at=time.time(), ended_by="approval_registration", approval_operation_id=operation_id)
                self.save("timer_stop", timer_id=span_id, phase="review_wait", cause="approval_registration")
            self.done(operation_id)
            return self.status()

    def validate_native(self, task, context, evidence):
        require(isinstance(context, dict) and all(text(context.get(k)) for k in
                ("device_id", "system_version", "app_id", "account_ref")), "Native context is incomplete")
        require(context.get("package_sha256") == self.package(task)["sha256"], "Wrong installed package binding")
        if task.get("independent_points"):
            require(text(context.get("environment_digest")), "Independent native points need an environment_digest")
        if self.contract.get("demo"):
            require(context.get("synthetic") is True, "Demo must mark synthetic results")
        else:
            require(context.get("synthetic") is False and context.get("normal_entry") is True,
                    "Native verification requires normal, non-injected entry")
        require(any(screenshot(e) for e in evidence), "Each checked native point needs a screenshot")
        require(any(Path(e["path"]).suffix.lower() in {".log", ".jsonl"} for e in evidence),
                "Each checked native point needs a command log")

    def validate_result(self, task, raw):
        from visual_binding import require_real_binding
        require_real_binding(self.contract, task)
        if task["kind"] == "build":
            evidence, extra = self._validate_build_result(task, raw)
            if task.get("execution_policy", {}).get("build_manifest"):
                extra["output_manifest"] = validate_build_manifest(raw, evidence)
            return evidence, extra
        require(isinstance(raw, dict) and isinstance(raw.get("points"), list), "Harness requires point-level results")
        incoming = {}
        for point in raw["points"]:
            require(isinstance(point, dict), "Point must be an object")
            aid = point.get("acceptance_id")
            require(aid in task["acceptance_ids"] and aid not in incoming, "Unknown or duplicate point")
            incoming[aid] = point
        results, evidence = [], []
        for aid in task["acceptance_ids"]:
            point = incoming.get(aid)
            if point is None:
                results.append({"acceptance_id": aid, "status": "not_checked", "reason": "Executor omitted this point",
                                "failure": failure_record({"category": "evidence_missing", "code": "omitted_point",
                                                           "step": aid, "reason": "Executor omitted this point"})})
                continue
            state = point.get("status")
            require(state in {"checked", "not_checked", "blocked"}, "Adapter reports checked, not_checked or blocked, never passed")
            if state != "checked":
                require(text(point.get("reason")) and not point.get("observations"), "Unchecked point needs reason and no observations")
                records = self.evidence(point["evidence"]) if point.get("evidence") else []
                failure = failure_record(point.get("failure") or {"category": "precondition_missing" if state == "blocked" else "evidence_missing",
                    "code": state, "step": aid, "reason": point["reason"]})
                results.append({"acceptance_id": aid, "status": state, "reason": point["reason"], "evidence": records, "failure": failure})
                evidence.extend(records)
                continue
            if task["kind"] == "visual":
                for sid in covered_states(self.acceptance[aid]):
                    require(self.states[sid]["baselines"],
                            "Visual point has no baseline; report blocked or not_checked: " + aid)
                    for baseline in self.states[sid]["baselines"]:
                        baseline_artifact(baseline, self.contract["roots"])
            records = self.evidence(point.get("evidence"))
            if task["kind"] in {"device", "visual"}:
                self.validate_native(task, raw.get("context"), records)
                validate_device_guard(task, raw, point, records)
                if task.get("device_suite"):
                    from suite import validate_point
                    validate_point(task, raw, point, records, self.contract["roots"])
                if "visual_comparison" in task:
                    from visual_binding import validate_point as validate_visual_point
                    validate_visual_point(self, task, raw, point, records)
            policy_matches = validate_policy(task, raw, point, records, self.contract["roots"])
            expected = {a["id"]: a for a in self.acceptance[aid]["assertions"]}
            observed = point.get("observations")
            require(isinstance(observed, list), "Checked point needs observations")
            checks, seen = [], set()
            for observation in observed:
                require(isinstance(observation, dict), "Observation must be an object")
                key = observation.get("assertion_id")
                require(key in expected and key not in seen and "actual" in observation, "Unknown or duplicate assertion")
                seen.add(key)
                assertion = expected[key]
                actual = observation["actual"]
                matches = actual is True if assertion["operator"] == "truthy" else runtime.digest(actual) == runtime.digest(assertion["expected"])
                checks.append({"assertion_id": key, "actual": actual, "expected": assertion["expected"], "passed": matches})
            outcome = "failed" if not policy_matches or any(not c["passed"] for c in checks) else "passed" if seen == set(expected) else "not_checked"
            evaluations = point.get("evaluations", [])
            require(isinstance(evaluations, list), "Evaluations must be a list")
            for evaluation in evaluations:
                require(isinstance(evaluation, dict) and all(text(evaluation.get(k)) for k in ("provider", "version", "metric")),
                        "Evaluation needs provider, version and metric")
                value, maximum = evaluation.get("value"), evaluation.get("maximum")
                require(type(value) in (int, float) and type(maximum) in (int, float)
                        and math.isfinite(value) and math.isfinite(maximum) and 0 <= value <= maximum and maximum > 0,
                        "Invalid evaluation score")
                require(self.evidence(evaluation.get("evidence")) and all(e in records for e in self.evidence(evaluation["evidence"])),
                        "Evaluation evidence must belong to this point")
            results.append({"acceptance_id": aid, "status": outcome, "checks": checks,
                            **({"failure": failure_record(point.get("failure") or {
                                "category": "product_failure" if outcome == "failed" else "evidence_missing",
                                "code": "verification_policy" if not policy_matches else "assertions", "step": aid,
                                "reason": "Observed request/state differs from verification policy" if not policy_matches else "Required assertions did not pass"})}
                               if outcome != "passed" else {}),
                            "missing_assertions": sorted(set(expected)-seen), "evidence": records,
                            "evaluations": evaluations, "evaluations_are_advisory": True})
            evidence.extend(records)
        unique = {e["path"]: e for e in evidence}
        return list(unique.values()), {"point_results": results, "accepted": all(p["status"] == "passed" for p in results),
                                      "context": raw.get("context"),
                                      "failures": [p["failure"] for p in results if p.get("failure")]}

    def commit_receipt(self, task, fingerprint, evidence, extra, attempt_dir):
        inputs = self.input_snapshot(task)
        require(runtime.digest(inputs) == fingerprint, "Inputs changed before receipt commit")
        for record in evidence:
            check_record(record)
        receipt = {"format": FORMAT, "task_id": task["id"], "kind": task["kind"],
                   "input_digest": fingerprint, "inputs": inputs, "evidence": evidence,
                   "recorded_at": time.time(), **extra}
        path = attempt_dir / "receipt.json"
        runtime.write_json(path, receipt)
        saved = self.state["tasks"].setdefault(task["id"], {})
        accepted = extra.get("accepted", True)
        saved.update(receipt=file_record(path), running=None, retry_allowed=False,
                     failure_digest=None if accepted else fingerprint)
        if "test_plan" in self.contract:
            unresolved = saved.setdefault("unresolved_failures", {})
            for point in extra.get("point_results", []):
                if point["status"] == "failed":
                    unresolved[point["acceptance_id"]] = saved["receipt"]
                elif point["status"] == "passed":
                    for history in self.state["tasks"].values():
                        history.get("unresolved_failures", {}).pop(point["acceptance_id"], None)
        if not accepted:
            self.note_failure(task, fingerprint, attempt_dir, extra.get("failures"))
            saved["error"] = "Required points failed, were not checked, or were blocked"
        else:
            for key in set(saved.pop("active_problem_ids", []) + saved.get("problem_history_ids", [])):
                self.state["problems"][key].update(status="resolved", resolved_by=saved["receipt"])
        self.save("task_passed" if accepted else "task_incomplete", task_id=task["id"], receipt=saved["receipt"])

    def finish_attempt(self, task, fingerprint, output, process):
        # Receipt references and phase totals must commit together, including on recovery.
        previous = copy.deepcopy(self.state)
        try:
            self._finish_attempt(task, fingerprint, output, process)
        except BaseException:
            self.state = previous
            raise

    def check_attempt_process(self, running, output, process):
        check_record(running["request"])
        require(Path(running["request"]["path"]) == output / "request.json", "Request belongs to another attempt")
        request = read_json(running["request"]["path"])
        require(request["input_digest"] == running["input_digest"], "Attempt request changed")
        substitutions = {"{request}": str(output / "request.json"), "{result}": str(output / "result.json")}
        substitutions.update({"{"+k+"}": v for k, v in request["roots"].items()})
        command = request["task"]["command"]
        expected_argv = [substitutions.get(a, a) for a in command["argv"]]
        require(process.get("argv") == expected_argv and
                process.get("cwd") == request["roots"][command["cwd"]] and
                process.get("log_path") == str(output / "process.log"), "Process record belongs to another attempt")
        return request

    def confirm_process_stopped(self, process):
        require(os.name == "posix", "Missing terminal record recovery requires POSIX process checks")
        pid = process.get("pid")
        require(type(pid) is int and pid > 1, "Missing valid process identity; cannot authorize retry")
        # The runtime creates a new session with PGID == PID. Reused IDs also block retry.
        for probe in (os.kill, os.killpg):
            try:
                probe(pid, 0)
            except ProcessLookupError:
                continue
            except OSError as error:
                raise ValueError("Cannot verify that the adapter process and group have stopped") from error
            raise ValueError("Adapter process or process group still exists; cannot authorize retry")
        return {"pid": pid, "process_absent": True, "process_group_absent": True, "checked_at": time.time()}

    def _finish_attempt(self, task, fingerprint, output, process):
        if process["status"] != "exited" or process["exit_code"] != 0:
            failure = {"category": "tool_failure", "code": "process_failed", "step": task["id"],
                       "reason": "Executor did not exit successfully"}
            try:
                value = read_json(output / "result.json").get("failure")
                if value:
                    failure = failure_record(value)
            except (OSError, ValueError, KeyError, TypeError):
                pass
            raise ExecutionFailure(**{k: failure.get(k) for k in ("category", "code", "step", "reason", "recovery_point")})
        require(self.fingerprint(task) == fingerprint, "Inputs changed during execution")
        saved = self.state["tasks"][task["id"]]
        running = saved["running"]
        request = self.check_attempt_process(running, output, process)
        require(request["input_digest"] == fingerprint and request["task"] == task, "Attempt request changed")
        require(process["log_sha256"] == runtime.file_hash(output / "process.log"), "Process log changed")
        raw = read_json(output / "result.json")
        phases = timings(raw.get("phase_seconds", {}))
        require(sum(phases.values()) <= process["elapsed_seconds"] + .05, "Phase times exceed executor duration")
        evidence, extra = self.validate_result(task, raw)
        validate_execution_window(task, raw, request, process)
        extra["problem_ids"] = request.get("problem_ids", [])
        if task["kind"] == "build":
            require(base.inside(Path(extra["package"]["path"]), output), "Build must output a fresh candidate")
        execution_evidence = [file_record(output / name) for name in ("result.json", "request.json", "process.json")]
        if (output / "process.log").stat().st_size:
            execution_evidence.append(file_record(output / "process.log"))
        if task.get("independent_points"):
            prior = self.current_points(task, fingerprint)
            supplied = {p["acceptance_id"] for p in raw["points"]}
            requested = set(request["requested_acceptance_ids"])
            context = extra.get("context")
            point_records = {}
            for index, result in enumerate(extra["point_results"]):
                aid = result["acceptance_id"]
                previous = prior.get(aid)
                if (aid not in supplied and aid not in requested and previous
                        and previous["result"]["status"] == "passed" and previous["context"] == context):
                    extra["point_results"][index] = previous["result"]
                    point_records[aid] = saved["point_receipts"][aid]
                    evidence.extend(previous["evidence"])
                else:
                    path = output / "points" / (aid + ".json")
                    point = {"input_digest": fingerprint, "context": context, "result": result,
                             "evidence": result.get("evidence", []) + execution_evidence}
                    runtime.write_json(path, point)
                    point_records[aid] = file_record(path)
                evidence.append(point_records[aid])
            extra["accepted"] = all(p["status"] == "passed" for p in extra["point_results"])
            extra["failures"] = [p["failure"] for p in extra["point_results"] if p.get("failure")]
            # These references are persisted together with the aggregate receipt.
            saved.update(point_receipts=point_records, point_context=context)
        for phase, seconds in phases.items():
            saved.setdefault("phase_seconds", {})[phase] = saved.get("phase_seconds", {}).get(phase, 0) + seconds
        evidence.extend(execution_evidence)
        evidence = list({r["path"]: r for r in evidence}.values())
        if output.name in saved.get("execution_receipts", {}):
            saved["execution_receipts"][output.name].update(phase_seconds=phases, accepted=extra.get("accepted", True))
        self.commit_receipt(task, fingerprint, evidence, {**extra, "phase_seconds": phases}, output)

    def account_process(self, task_id, output, process):
        saved = self.state["tasks"][task_id]
        accounted = saved.setdefault("accounted_processes", [])
        if output.name not in accounted:
            saved["execution_seconds"] = saved.get("execution_seconds", 0) + process["elapsed_seconds"]
            accounted.append(output.name)
            saved.setdefault("execution_receipts", {})[output.name] = {"seconds": process["elapsed_seconds"],
                "started_at": process.get("started_at"), "finished_at": process.get("finished_at"),
                "process_status": process["status"], "cost_category": saved.get("running", {}).get("cost_category", "execution"),
                "problem_ids": saved.get("running", {}).get("problem_ids", [])}

    def execute(self, task_id, fingerprint, point_ids=None, problem_ids=None):
        task = self.tasks[task_id]
        saved = self.state["tasks"].setdefault(task_id, {})
        linked_problems = sorted(set((problem_ids or []) + saved.get("active_problem_ids", [])))
        prior = self.current_points(task, fingerprint) if task.get("independent_points") else {}
        requested = list(task["acceptance_ids"])
        if task.get("independent_points"):
            requested = (list(point_ids) if point_ids else
                         [aid for aid in requested if prior.get(aid, {}).get("result", {}).get("status") != "passed"])
        idempotency_key = runtime.digest({"run_id": self.state["run_id"], "task_id": task_id, "input_digest": fingerprint})
        saved["attempts"] = saved.get("attempts", 0) + 1
        saved.pop("receipt", None)
        output = self.attempt_dir(task_id)
        request_path, result_path = output / "request.json", output / "result.json"
        state_ids = {sid for a in task["acceptance_ids"] for sid in covered_states(self.acceptance[a])}
        request = {"format": FORMAT, "task": task, "input_digest": fingerprint, "roots": self.contract["roots"],
                   "demo": self.contract.get("demo", False), "acceptance": [self.acceptance[a] for a in task["acceptance_ids"]],
                   "states": [self.states[s] for s in sorted(state_ids - {None})], "package": self.package(task),
                   "idempotency_key": idempotency_key,
                   "problem_ids": linked_problems,
                   "point_idempotency_keys": {aid: runtime.digest([idempotency_key, aid]) for aid in task["acceptance_ids"]},
                   "requested_acceptance_ids": requested, "reuse_context": saved.get("point_context") if prior else None,
                   "dependency_receipts": {d: self.state["tasks"][d]["receipt"] for d in task["depends_on"]}}
        if task.get("project_capability_ids"):
            profile, records = profile_data(self.contract)
            request["project_execution"] = project_readiness(profile, self.contract["roots"], records[0]["path"],
                self.contract.get("demo", False), task["project_capability_ids"])["capabilities"]
            for capability in request["project_execution"]:
                receipt = capability["receipt"]
                self.state.setdefault("project_setup_receipts", {})[receipt["sha256"]] = receipt
        runtime.write_json(request_path, request)
        saved.update(running={"input_digest": fingerprint, "directory": str(output),
                              "request": file_record(request_path),
                              "problem_ids": linked_problems,
                              "cost_category": "rework" if linked_problems else "execution"}, retry_allowed=False)
        self.save("task_started", task_id=task_id)
        process = None
        try:
            substitutions = {"{request}": str(request_path), "{result}": str(result_path)}
            substitutions.update({"{"+k+"}": v for k, v in self.contract["roots"].items()})
            process = runtime.run_process([substitutions.get(a, a) for a in task["command"]["argv"]],
                cwd=Path(self.contract["roots"][task["command"]["cwd"]]), log_path=output / "process.log",
                progress_path=output / "process.json", timeout_seconds=task["command"].get("timeout_seconds", 120),
                idle_timeout_seconds=task["command"].get("timeout_seconds", 120))
            self.account_process(task_id, output, process)
            self.finish_attempt(task, fingerprint, output, process)
        except (OSError, ValueError, KeyError, TypeError) as error:
            saved = self.state["tasks"][task_id]
            if process is None or isinstance(error, OSError):
                saved.update(error=str(error), retry_allowed=False)
                self.save("attempt_in_doubt", task_id=task_id, error=str(error), directory=str(output))
                return
            self.note_failure(task, fingerprint, output, [error.failure] if isinstance(error, ExecutionFailure) else None)
            saved.update(running=None, failure_digest=fingerprint, error=str(error))
            runtime.write_json(output / "failure.json", {"task_id": task_id, "error": str(error), "input_digest": fingerprint})
            self.save("task_failed", task_id=task_id, error=str(error), directory=str(output))

    def run(self, operation_id, limit=20, task_ids=None, point_ids=None, problem_ids=None):
        try:
            return self._run(operation_id, limit, task_ids, point_ids, problem_ids)
        except (KeyboardInterrupt, SystemExit):
            from session import FILE, load, record
            try:
                if (self.root / FILE).is_file():
                    with self.locked():
                        journal = load(self.root)
                        sid = journal.get("current_session_id")
                        if sid and journal["sessions"][sid]["state"] == "working":
                            record(self.root, "interrupt-" + uuid.uuid4().hex, {"action": "interrupt", "session_id": sid,
                                   "summary": "Run interrupted before returning; inspect existing process/results before replay"}, self)
            except (OSError, ValueError, KeyError, TypeError):
                pass
            raise

    def _run(self, operation_id, limit=20, task_ids=None, point_ids=None, problem_ids=None):
        require(type(limit) is int and 1 <= limit <= 100, "Batch limit must be 1..100")
        task_ids = task_ids or []
        require(ids(task_ids) and set(task_ids) <= set(self.tasks), "Unknown/duplicate selected task")
        point_ids = point_ids or []
        require(ids(point_ids) and set(point_ids) <= set(self.acceptance), "Unknown/duplicate selected point")
        require(not (point_ids and task_ids), "Select tasks or points, not both")
        owners = [t["id"] for t in self.tasks.values() if set(point_ids) & set(t["acceptance_ids"])]
        require(all(self.tasks[t].get("independent_points") for t in owners), "Point selection requires independent_points")
        with self.locked():
            require(ids(problem_ids or []) and set(problem_ids or []) <= set(self.state.get("problems", {})), "Unknown problem ID")
            request = {"command": "run", "limit": limit, "task_ids": task_ids}
            if problem_ids:
                request["problem_ids"] = problem_ids
            if point_ids:
                request["point_ids"] = point_ids
            replay = self.replay(operation_id, request)
            if replay:
                return replay
            selected_ids = task_ids or owners
            selected = set(selected_ids) if selected_ids else set(self.tasks)
            for tid in selected_ids:
                selected.update(base.ancestors(tid, self.tasks))
            executed = []
            priority = {"contract": 0, "build": 1, "device": 2, "visual": 3}
            while len(executed) < limit:
                current = self.status()
                ready = [r for r in current["tasks"] if r["id"] in current["ready_tasks"]
                         and r["id"] in selected and r["id"] not in executed]
                if not ready:
                    break
                row = min(ready, key=lambda r: (priority[r["kind"]], r["id"]))
                chosen = [aid for aid in point_ids if aid in self.tasks[row["id"]]["acceptance_ids"]]
                self.execute(row["id"], row["input_digest"], chosen, problem_ids)
                executed.append(row["id"])
            self.done(operation_id)
            return {"executed": executed, **self.status()}

    def retry(self, operation_id, task_id, reason, evidence_paths):
        with self.locked():
            require(task_id in self.tasks and text(reason), "Retry requires task and diagnosis")
            evidence = self.evidence(evidence_paths)
            request = {"command": "retry", "task_id": task_id, "reason": reason, "evidence": evidence}
            if operation_id in self.state["operations"]:
                return self.replay(operation_id, request)
            saved = self.state["tasks"].get(task_id, {})
            running = saved.get("running")
            require(running or saved.get("failure_digest"), "No failed or interrupted attempt to reconcile")
            require(not running or not any((Path(running["directory"]) / name).exists()
                                           for name in ("result.json", "process.json")),
                    "Interrupted attempt has execution evidence; use reconcile before permitting another execution")
            diagnoses = []
            for key in saved.get("active_problem_ids", []):
                problem = self.state["problems"][key]
                diagnosis = {"reason": reason, "evidence_hashes": sorted({e["sha256"] for e in evidence})}
                require(not problem["diagnoses"] or any(problem["diagnoses"][-1].get(k) != v for k, v in diagnosis.items()),
                        "Same problem needs new diagnostic evidence or a changed strategy before retry")
                diagnoses.append((problem, diagnosis))
            self.replay(operation_id, request)
            for problem, diagnosis in diagnoses:
                problem["diagnoses"].append({**diagnosis, "recorded_at": time.time(), "operation_id": operation_id,
                                             "after_occurrence": problem["occurrences"]})
            if running:
                self.note_failure(self.tasks[task_id], running["input_digest"], Path(running["directory"]))
            saved.update(running=None, retry_allowed=True)
            self.save("retry_authorized", task_id=task_id, reason=reason, evidence=evidence)
            self.done(operation_id)
            return self.status()

    def reconcile(self, operation_id, task_id, reason, evidence_paths, action="accept"):
        with self.locked():
            require(task_id in self.tasks and text(reason) and action in {"accept", "retry"},
                    "Reconciliation requires task, action and diagnosis")
            evidence = self.evidence(evidence_paths)
            request = {"command": "reconcile", "task_id": task_id, "reason": reason,
                       "evidence": evidence, "action": action}
            if operation_id in self.state["operations"]:
                return self.replay(operation_id, request)
            task = self.tasks[task_id]
            saved = self.state["tasks"].get(task_id, {})
            running = saved.get("running")
            require(running, "No interrupted attempt")
            output = Path(running["directory"]).resolve(strict=True)
            require(base.inside(output, self.root / "attempts"), "Attempt escapes run")
            process = read_json(output / "process.json")
            self.check_attempt_process(running, output, process)
            terminal = process.get("status") in {"exited", "interrupted", "timed_out", "idle_timeout"}
            stopped = None
            if not terminal:
                require(action == "retry", "Process has no terminal record; only explicit retry after stop verification is allowed")
                require(process.get("status") == "running", "Unknown process status; cannot authorize retry")
                stopped = self.confirm_process_stopped(process)
            if action == "accept":
                rows = {r["id"]: r for r in self.status()["tasks"]}
                require(all(rows[d]["status"] == "passed" for d in task["depends_on"]),
                        "Dependencies must still pass before reconciliation")
                require(self.fingerprint(task) == running["input_digest"], "Inputs changed; cannot accept old result")
                require(process.get("status") == "exited" and process.get("exit_code") == 0,
                        "Only a successfully exited adapter result can be accepted")
            self.replay(operation_id, request)
            if action == "accept":
                self.account_process(task_id, output, process)
                self.finish_attempt(task, running["input_digest"], output, process)
            else:
                self.note_failure(task, running["input_digest"], output)
                saved.update(running=None, failure_digest=running["input_digest"], retry_allowed=True)
            self.save("attempt_reconciled", task_id=task_id, action=action, reason=reason, evidence=evidence,
                      directory=str(output), process_record=file_record(output / "process.json"), stop_verification=stopped)
            self.done(operation_id)
            return self.status()

    def timer(self, operation_id, span_id, phase, action):
        require(base.identifier(span_id) and phase in AGENT_PHASES and action in {"start", "stop"}, "Invalid timer")
        with self.locked():
            request = {"command": "timer", "id": span_id, "phase": phase, "action": action}
            if operation_id in self.state["operations"]:
                return self.replay(operation_id, request)
            if action == "start":
                require(span_id not in self.state["timers"], "Timer ID already exists")
                require(all("ended_at" in s or (s["phase"] == "review_wait") != (phase == "review_wait")
                            for s in self.state["timers"].values()), "Stop the active agent timer first")
            else:
                span = self.state["timers"].get(span_id)
                require(span and span["phase"] == phase and "ended_at" not in span, "No matching active timer")
            self.replay(operation_id, request)
            if action == "start":
                self.state["timers"][span_id] = {"phase": phase, "started_at": time.time()}
                if phase == "review_wait":
                    status = self.status()
                    waiting = {g["capability_id"]: g["input_digest"] for g in status["joint_review"]
                               if g["status"] in {"awaiting_human", "stale"} and g.get("input_digest")}
                    self.state["timers"][span_id].update(gate="joint" if waiting else "final",
                        review_inputs=waiting or {"final": status["final_review"].get("input_digest")})
            else:
                self.state["timers"][span_id]["ended_at"] = time.time()
            self.save("timer_" + action, timer_id=span_id, phase=phase)
            self.done(operation_id)
            return self.metrics()

    def revise(self, operation_id, contract_path, reason):
        path = Path(contract_path).resolve(strict=True)
        new = validate_contract(read_json(path), path.parent)
        with self.locked():
            require(text(reason), "Revision needs a reason")
            require(all(new.get(k) == self.contract.get(k) for k in ("feature_id", "roots", "demo", "policy")),
                    "Cannot change run identity or demo mode")
            planned = "test_plan" in self.contract or "test_plan" in new
            if planned:
                latest_results = {}
                unresolved = set()
                for saved in self.state["tasks"].values():
                    for aid, record in saved.get("unresolved_failures", {}).items():
                        check_record(record)
                        unresolved.add(aid)
                    if saved.get("receipt"):
                        check_record(saved["receipt"])
                        receipt = read_json(saved["receipt"]["path"])
                        latest_results.update({p["acceptance_id"]: p["status"] for p in receipt.get("point_results", [])})
                    for point in saved.get("point_receipts", {}).values():
                        check_record(point)
                        result = read_json(point["path"])["result"]
                        latest_results[result["acceptance_id"]] = result["status"]
                validate_revision(self.contract, new, unresolved | {aid for aid, outcome in latest_results.items() if outcome == "failed"})
            for key in (("capabilities",) if planned else ("capabilities", "acceptance")):
                incoming = {r["id"]: r for r in new[key]}
                require(all(r["id"] in incoming and r == incoming[r["id"]] for r in self.contract[key]),
                        "Revision cannot remove/change existing " + key)
            incoming_states = {r["id"]: r for r in new["states"]}
            for old_state in self.states.values():
                updated = incoming_states.get(old_state["id"])
                same_behavior = updated is not None and {
                    k: v for k, v in old_state.items() if k not in {"baselines", "baseline_gap"}
                } == {k: v for k, v in updated.items() if k not in {"baselines", "baseline_gap"}}
                resolves_gap = same_behavior and not old_state["baselines"] and updated["baselines"]
                require(updated == old_state or resolves_gap, "Revision cannot remove/change existing states")
            incoming = {t["id"]: t for t in new["tasks"]}
            removed_tasks = set(self.tasks) - set(incoming)
            require(not removed_tasks or planned and all(self.tasks[t]["kind"] in base.TEST_KINDS for t in removed_tasks),
                    "Revision cannot remove non-test tasks")
            for tid, old in self.tasks.items():
                if tid not in incoming:
                    continue
                task = incoming[tid]
                require(old["kind"] == task["kind"] and old.get("max_attempts", 5) == task.get("max_attempts", 5),
                        "Revision cannot change task identity or reset budget")
                require((planned or set(old["acceptance_ids"]) <= set(task["acceptance_ids"]))
                        and set(old["capability_ids"]) <= set(task["capability_ids"]), "Revision cannot reduce task coverage")
            for tid, old in self.state.get("retired_tasks", {}).items():
                if tid in incoming:
                    require(incoming[tid]["kind"] == old["kind"] and incoming[tid].get("max_attempts", 5) == old.get("max_attempts", 5),
                            "Retired task identity and failure budget must be preserved")
            require(not any(t.get("running") for t in self.state["tasks"].values()), "Reconcile interrupted tasks first")
            replay = self.replay(operation_id, {"command": "revise", "contract": new, "reason": reason})
            if replay:
                return replay
            self.state["revision"] += 1
            target = self.root / "contracts" / (str(self.state["revision"]).zfill(3) + ".json")
            runtime.write_json(target, new)
            if removed_tasks:
                self.state.setdefault("retired_tasks", {}).update({tid: self.tasks[tid] for tid in removed_tasks})
            if planned and new["test_plan"] != self.contract["test_plan"]:
                # Carry failed checks through equivalent replacements until a real pass resolves them.
                owners = {aid: t["id"] for t in new["tasks"] for aid in t["acceptance_ids"]}
                failures = {aid: record for saved in self.state["tasks"].values()
                            for aid, record in saved.get("unresolved_failures", {}).items()}
                for replacement in new["test_plan"]["revision"]["replacements"]:
                    record = failures.get(replacement["acceptance_id"])
                    if record:
                        for aid in replacement["replacement_ids"]:
                            saved = self.state["tasks"].setdefault(owners[aid], {})
                            saved.setdefault("unresolved_failures", {})[aid] = record
            self.state["contract"] = file_record(target)
            self.save("contract_revised", reason=reason, contract=self.state["contract"])
            self.done(operation_id)
            self.__init__(self.root)
            return self.status()

    def verify(self):
        with self.locked():
            result = self.status()
            result["limitations"] = [
                "Coverage applies to the declared state inventory; semantic completeness requires human review.",
                "Local approval records require trusted human provenance; this CLI cannot authenticate a person.",
                "Scores are advisory. Built-in pixel comparison is opt-in; no Huawei provider or device attestation is bundled."]
            runtime.write_json(self.root / "assessment.json", result)
            return result

    @contextmanager
    def locked(self):
        with runtime.file_lock(self.root / "harness.lock", timeout=0) as acquired:
            require(acquired, "Harness busy; inspect attempts/*/process.json for progress")
            self.__init__(self.root)
            yield


    def replay(self, operation_id, request):
        require(identifier(operation_id), "Invalid operation ID")
        saved = self.state["operations"].get(operation_id)
        if saved:
            require(saved["request_digest"] == runtime.digest(request), "Operation ID reused with different input")
            return {"replayed": True, "operation_status": saved["status"], "next": "status"}
        self.state["operations"][operation_id] = {"request_digest": runtime.digest(request), "status": "started"}
        self.save("operation_started", operation_id=operation_id)
        return None


    def done(self, operation_id):
        self.state["operations"][operation_id]["status"] = "finished"
        self.save("operation_finished", operation_id=operation_id)


    def receipt(self, task_id):
        record = self.state["tasks"][task_id]["receipt"]
        check_record(record)
        return read_json(record["path"])


    def _source_snapshot(self, task):
        groups = task["inputs"] + (self.contract["production_inputs"] if task["kind"] == "build" else [])
        paths = set(input_files(groups, self.contract["roots"]))
        paths.update((Path(__file__).resolve(), Path(runtime.__file__).resolve()))
        for cid in task["capability_ids"]:
            for source in self.caps[cid]["android_sources"]:
                paths.add(relative_file(Path(self.contract["roots"]["android"]), source["path"]))
        if task["kind"] not in AGENT_KINDS:
            paths.add(Path(task["command"]["argv"][0]).resolve(strict=True))
            cwd = Path(self.contract["roots"][task["command"]["cwd"]])
            for argument in task["command"]["argv"][1:]:
                if argument not in {"{request}", "{result}", "{android}", "{harmony}", "{tools}"}:
                    candidate = cwd / argument
                    if candidate.is_file():
                        paths.add(candidate.resolve(strict=True))
        return {"task": task, "roots": self.contract["roots"],
            "policy": self.contract["policy"], "demo": self.contract.get("demo", False),
            "capabilities": [self.caps[c] for c in task["capability_ids"]],
            "acceptance": [self.acceptance[a] for a in task["acceptance_ids"]],
            "files": [file_record(p, allow_empty=True) for p in sorted(paths)],
            "dependencies": {d: self.state["tasks"][d]["receipt"]["sha256"] for d in task["depends_on"]}}


    def fingerprint(self, task):
        return runtime.digest(self.input_snapshot(task))


    def evidence(self, paths):
        require(isinstance(paths, list) and paths, "Non-empty evidence paths required")
        allowed = [self.root, *(Path(p) for p in self.contract["roots"].values())]
        records = []
        for value in paths:
            require(text(value), "Evidence paths must be strings")
            path = Path(value)
            require(path.is_absolute() and any(inside(path.resolve(), r) for r in allowed),
                    "Evidence must be inside this run or declared sources")
            records.append(file_record(path))
        return sorted({r["path"]: r for r in records}.values(), key=lambda r: r["path"])


    def package(self, task):
        builds = [t for t in ancestors(task["id"], self.tasks) if self.tasks[t]["kind"] == "build"]
        return self.receipt(builds[0])["package"] if builds else None


    def _validate_build_result(self, task, raw):
        require(isinstance(raw, dict), "Executor result must be an object")
        evidence = self.evidence(raw.get("evidence"))
        package = file_record(raw["package_path"])
        require(package in evidence, "Build package must appear in evidence")
        require(Path(package["path"]).suffix.lower() in {".hap", ".app", ".hsp"}, "Expected Harmony build artifact")
        if not self.contract.get("demo"):
            require(zipfile.is_zipfile(package["path"]), "Harmony build artifact must be a ZIP package")
        return evidence, {"package": package}

    def attempt_dir(self, task_id):
        target = self.root / "attempts" / (task_id + "-" + uuid.uuid4().hex)
        target.mkdir(parents=True)
        return target


    def record(self, operation_id, result):
        with self.locked():
            tid = result["task_id"]
            require(tid in self.tasks and self.tasks[tid]["kind"] in AGENT_KINDS,
                    "Only discovery, implementation and review use agent records")
            row = next(r for r in self.status()["tasks"] if r["id"] == tid)
            replay = self.state["operations"].get(operation_id)
            if replay:
                return self.replay(operation_id, {"command": "record", "result": result})
            require(row["status"] == "agent_required", "Task is not ready for an agent record")
            require(result.get("input_digest") == row["input_digest"], "Agent record uses a stale input digest")
            require(text(result.get("summary")), "Agent record needs a concrete conclusion")
            evidence = self.evidence(result.get("evidence"))
            task = self.tasks[tid]
            if task["kind"] == "review":
                require(result.get("findings") == [] and ids(result.get("acceptance_ids"))
                        and set(result["acceptance_ids"]) == set(self.acceptance)
                        and text(result.get("reviewer")), "Review must cover all acceptance with no unresolved findings")
            self.replay(operation_id, {"command": "record", "result": result})
            self.commit_receipt(task, row["input_digest"], evidence, {"agent_record": result}, self.attempt_dir(tid))
            self.done(operation_id)
            return self.status()


    def inspect(self, task_id):
        with self.locked():
            require(task_id in self.tasks, "Unknown task")
            row = next(r for r in self.status()["tasks"] if r["id"] == task_id)
            result = {"task": row, "definition": self.tasks[task_id],
                      "acceptance": [self.acceptance[a] for a in self.tasks[task_id]["acceptance_ids"]]}
            saved = self.state["tasks"].get(task_id, {})
            if saved.get("receipt"):
                result["receipt"] = self.receipt(task_id)
            result["attempt_directories"] = [str(p) for p in sorted((self.root / "attempts").glob(task_id + "-*"))]
            return result


    def attach(self, operation_id, task_id, evidence_paths):
        with self.locked():
            require(task_id in self.tasks, "Unknown task")
            evidence = self.evidence(evidence_paths)
            request = {"command": "attach", "task_id": task_id, "evidence": evidence}
            if operation_id in self.state["operations"]:
                return self.replay(operation_id, request)
            row = next(r for r in self.status()["tasks"] if r["id"] == task_id)
            require(row["status"] == "passed", "Only a current passed task can receive supplemental evidence")
            receipt = self.receipt(task_id)
            self.replay(operation_id, request)
            extra = {k: v for k, v in receipt.items() if k not in
                     {"format", "task_id", "kind", "input_digest", "inputs", "evidence", "recorded_at"}}
            self.commit_receipt(self.tasks[task_id], row["input_digest"], receipt["evidence"] + evidence,
                                extra, self.attempt_dir(task_id))
            self.done(operation_id)
            return self.status()
