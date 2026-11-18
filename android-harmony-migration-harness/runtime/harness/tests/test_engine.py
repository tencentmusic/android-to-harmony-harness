import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))
from engine import Harness, read_json, runtime
from contract import FORMAT, validate_contract
from demo import prepare, record_demo, approval_demo
from report import markdown


class ReviewedHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "case"
        self.contract_path = prepare(self.root)
        self.contract = read_json(self.contract_path)

    def init(self):
        runtime.write_json(self.contract_path, self.contract)
        self.experiment = Harness.init(self.contract_path, self.root / "run")
        return self.experiment

    def ready(self):
        e = self.init()
        record_demo(e, "discover", "discover")
        approval_demo(e)
        record_demo(e, "implement", "implement")
        return e

    def execute_suite(self):
        e = self.ready()
        e.run("batch")
        return e

    def complete(self):
        e = self.execute_suite()
        record_demo(e, "review", "review")
        approval_demo(e, "final", "final")
        return e

    def rows(self, e=None):
        return {r["id"]: r for r in (e or self.experiment).status()["tasks"]}

    def invalid(self, message):
        with self.assertRaisesRegex(ValueError, message):
            validate_contract(self.contract, self.root)

    def test_joint_gate_prevents_implementation_and_build(self):
        e = self.init()
        record_demo(e, "discover", "discover")
        self.assertEqual(self.rows()["implement"]["status"], "awaiting_human")
        self.assertEqual(e.run("before-approval")["executed"], [])
        with self.assertRaisesRegex(ValueError, "not ready"):
            record_demo(e, "implement", "bypass")
        approval_demo(e)
        self.assertEqual(self.rows()["implement"]["status"], "agent_required")

    def test_final_human_gate_is_separate_from_agent_review(self):
        e = self.execute_suite()
        self.assertEqual(e.status()["coverage"]["passed"], 5)
        self.assertEqual(e.status()["final_review"]["status"], "blocked")
        record_demo(e, "review", "review")
        self.assertEqual(e.verify()["status"], "incomplete")
        self.assertEqual(e.status()["final_review"]["status"], "awaiting_human")
        approval_demo(e, "final", "final")
        self.assertEqual(e.verify()["status"], "demo_only")
        self.assertFalse((e.root / "manifest.json").exists())

    def test_missing_dialog_cannot_hide_in_batch_pass(self):
        (self.root / "tools/settings.json").write_text('{"omit_popup":true}')
        e = self.execute_suite()
        status = e.status()
        points = {p["acceptance_id"]: p for p in status["points"]}
        self.assertEqual(points["photo-source-visual"]["status"], "not_checked")
        self.assertEqual(points["editor-visual"]["status"], "passed")
        self.assertEqual(points["photo-source-device"]["status"], "passed")
        self.assertEqual(status["coverage"]["passed"], 4)
        self.assertEqual(status["coverage"]["not_checked"], 1)
        self.assertEqual(self.rows()["visual"]["status"], "needs_diagnosis")
        self.assertEqual(e.run("no-blind-retry")["executed"], [])
        self.assertIn("未检查", markdown(e))

    def test_high_score_does_not_replace_missing_assertion(self):
        e = self.ready()
        log = e.root / "observed.log"
        log.write_text("Score supplied, assertion missing\n")
        raw = {"points": [{"acceptance_id": "gifts", "status": "checked", "observations": [],
            "evidence": [str(log)], "evaluations": [{"provider": "test-only", "version": "1", "metric": "quality",
                "value": 100, "maximum": 100, "evidence": [str(log)]}]}]}
        _, result = e.validate_result(e.tasks["contracts"], raw)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["point_results"][0]["status"], "not_checked")

    def test_false_observation_cannot_self_report_passed(self):
        e = self.ready()
        log = e.root / "observed.log"
        log.write_text("99 observed\n")
        raw = {"points": [{"acceptance_id": "gifts", "status": "checked", "evidence": [str(log)],
            "observations": [{"assertion_id": "gift-values", "actual": [1, 10, 99], "expected": [1, 10, 99], "passed": True}]}]}
        _, result = e.validate_result(e.tasks["contracts"], raw)
        self.assertEqual(result["point_results"][0]["status"], "failed")

    def test_missing_point_evidence_is_rejected(self):
        e = self.ready()
        with self.assertRaisesRegex(ValueError, "evidence paths"):
            e.validate_result(e.tasks["contracts"], {"points": [{"acceptance_id": "gifts", "status": "checked",
                "observations": [{"assertion_id": "gift-values", "actual": [1, 10, 100]}]}]})

    def test_native_evidence_is_required_for_each_point(self):
        e = self.execute_suite()
        receipt = e.receipt("visual")
        raw = read_json(next(r["path"] for r in receipt["evidence"] if r["path"].endswith("/result.json")))
        raw["points"][1]["evidence"] = [p for p in raw["points"][1]["evidence"] if p.endswith(".log")]
        with self.assertRaisesRegex(ValueError, "Each checked native point needs a screenshot"):
            e.validate_result(e.tasks["visual"], raw)

    def test_wrong_package_binding_is_rejected(self):
        e = self.execute_suite()
        receipt = e.receipt("device")
        raw = read_json(next(r["path"] for r in receipt["evidence"] if r["path"].endswith("/result.json")))
        raw["context"]["package_sha256"] = "wrong"
        with self.assertRaisesRegex(ValueError, "Wrong installed package"):
            e.validate_result(e.tasks["device"], raw)

    def test_blocked_point_keeps_reason_and_cannot_be_approved(self):
        e = self.ready()
        _, result = e.validate_result(e.tasks["contracts"], {"points": [
            {"acceptance_id": "gifts", "status": "blocked", "reason": "No account condition"}]})
        self.assertEqual(result["point_results"][0]["status"], "blocked")
        self.assertFalse(result["accepted"])
        with self.assertRaisesRegex(ValueError, "Every required point"):
            approval_demo(e, "final", "premature-final")

    def test_schema_rejects_missing_state_visual_coverage(self):
        self.contract["acceptance"][-1]["state_id"] = "editor"
        self.invalid("photo-source lacks visual")

    def test_schema_rejects_native_point_without_state(self):
        self.contract["acceptance"][-1].pop("state_id")
        self.invalid("state_id")

    def test_review_scope_cannot_omit_popup(self):
        self.contract["review_scopes"][0]["state_ids"] = ["editor"]
        self.invalid("every state")

    def test_build_cannot_bypass_implementation_gate(self):
        self.contract["tasks"][3]["depends_on"] = ["discover"]
        # Native test dependency validation also rejects this shortcut.
        self.invalid("implementation dependency|all implementation")

    def test_ui_material_change_reopens_joint_gate(self):
        e = self.complete()
        (self.root / "tools/ui.md").write_text("Changed UI decisions\n")
        self.assertEqual(e.status()["joint_review"][0]["status"], "stale")
        self.assertEqual(self.rows()["implement"]["status"], "awaiting_human")
        self.assertEqual(self.rows()["discover"]["status"], "passed")
        self.assertEqual(e.verify()["status"], "incomplete")

    def test_material_change_does_not_revoke_unrelated_scope_approval(self):
        cap = copy.deepcopy(self.contract["capabilities"][0])
        cap["id"] = "independent"
        self.contract["capabilities"].append(cap)
        new_states = []
        for state in list(self.contract["states"]):
            cloned = copy.deepcopy(state)
            cloned.update(id="other-"+state["id"], capability_id="independent")
            new_states.append(cloned)
        self.contract["states"].extend(new_states)
        for point in list(self.contract["acceptance"]):
            cloned = copy.deepcopy(point)
            cloned.update(id="other-"+point["id"], capability_id="independent")
            if point.get("state_id"):
                cloned["state_id"] = "other-"+point["state_id"]
            self.contract["acceptance"].append(cloned)
        scope = copy.deepcopy(self.contract["review_scopes"][0])
        scope.update(id="independent", state_ids=[s["id"] for s in new_states])
        (self.root / "tools/other").mkdir()
        for dimension in ("requirements", "technical", "ui"):
            path = "other/"+dimension+".md"
            (self.root / "tools" / path).write_text("Independent review material\n")
            scope[dimension]["path"] = path
        self.contract["review_scopes"].append(scope)
        for task in self.contract["tasks"]:
            task["capability_ids"].append("independent")
            task["acceptance_ids"] += ["other-"+aid for aid in list(task["acceptance_ids"])]
        e = self.ready()
        (self.root / "tools/ui.md").write_text("Changed first scope only\n")
        gates = {r["capability_id"]: r["status"] for r in e.status()["joint_review"]}
        self.assertEqual(gates, {"dynamics": "stale", "independent": "approved"})

    def test_invalid_timing_cannot_be_reported_as_real_duration(self):
        adapter = self.root / "tools/adapter.py"
        adapter.write_text(adapter.read_text().replace("time.perf_counter()-started}", "99999}"))
        e = self.ready()
        e.run("invalid-timing", limit=1)
        self.assertIn("Phase times exceed", self.rows()["contracts"]["reason"])
        self.assertEqual(e.metrics()["adapter_reported_phase_seconds"], {})

    def test_source_text_cannot_replace_visual_baseline_image(self):
        self.contract["states"][0]["baselines"][0]["path"] = "reference.json"
        self.invalid("source image")

    def separate_declared_baseline(self):
        path = self.root / "android/declared.png"
        path.write_bytes((self.root / "android/baseline.png").read_bytes())
        for state in self.contract["states"]:
            state["baselines"][0]["path"] = path.name
        return path

    def test_corrupt_baseline_blocks_discovery_and_approval_until_restored(self):
        baseline = self.separate_declared_baseline()
        original = baseline.read_bytes()
        e = self.init()
        digest = e.joint_status("dynamics")["input_digest"]
        baseline.write_text("This is not an image")
        e = Harness(e.root)
        row = self.rows(e)["discover"]
        self.assertEqual(row["status"], "input_missing")
        self.assertIn("Invalid baseline image", row["reason"])
        gate = e.review_packet()["joint_review"][0]
        self.assertEqual(gate["status"], "stale")
        self.assertNotIn("input_digest", gate)
        self.assertIn("Invalid baseline image", gate["reason"])
        with self.assertRaisesRegex(ValueError, "not ready"):
            record_demo(e, "discover", "invalid-discovery")
        note = e.root / "approval.txt"
        note.write_text("Synthetic approval request for an invalid baseline")
        with self.assertRaisesRegex(ValueError, "Complete discovery"):
            e.approve("invalid-approval", {"gate": "joint", "actor_type": "demo",
                "reviewer": "test", "summary": "Synthetic review", "evidence": [str(note)],
                "reviews": [{"capability_id": "dynamics", "input_digest": digest,
                             "decisions": {d: "approved" for d in ("requirements", "technical", "ui")}}]})
        self.assertEqual(e.run("blocked")["executed"], [])
        baseline.write_bytes(original)
        record_demo(e, "discover", "discover")
        approval_demo(e)
        record_demo(e, "implement", "implement")
        e.run("recovered")
        record_demo(e, "review", "review")
        approval_demo(e, "final", "final")
        self.assertEqual(e.verify()["status"], "demo_only")

    def test_corrupt_baseline_revokes_completion_and_rejects_visual_results(self):
        baseline = self.separate_declared_baseline()
        e = self.complete()
        receipt = e.receipt("visual")
        raw = read_json(next(r["path"] for r in receipt["evidence"] if r["path"].endswith("/result.json")))
        baseline.write_text("This is not an image")
        e = Harness(e.root)
        with self.assertRaisesRegex(ValueError, "Invalid baseline image"):
            e.validate_result(e.tasks["visual"], raw)
        self.assertEqual(e.inspect("discover")["task"]["status"], "input_missing")
        status = e.verify()
        self.assertEqual(status["status"], "incomplete")
        self.assertEqual(status["final_review"]["status"], "blocked")
        self.assertEqual(status["coverage"]["passed"], 0)
        with self.assertRaisesRegex(ValueError, "Every required point"):
            approval_demo(e, "final", "invalid-final")
        result = subprocess.run([sys.executable, str(ROOT / "migration.py"), "verify", "--run-dir", str(e.root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "incomplete")

    def test_missing_baseline_remains_diagnosable_and_can_be_restored(self):
        baseline = self.separate_declared_baseline()
        original = baseline.read_bytes()
        e = self.ready()
        baseline.unlink()
        e = Harness(e.root)
        self.assertEqual(e.inspect("discover")["task"]["status"], "input_missing")
        self.assertIn("reason", e.review_packet()["joint_review"][0])
        self.assertEqual(e.verify()["status"], "incomplete")
        baseline.write_bytes(original)
        self.assertEqual(self.rows(e)["discover"]["status"], "passed")
        self.assertEqual(e.joint_status("dynamics")["status"], "approved")
        e.run("restored")
        record_demo(e, "review", "review")
        approval_demo(e, "final", "final")
        self.assertEqual(e.verify()["status"], "demo_only")

    def test_missing_baseline_requires_explicit_reason(self):
        state = self.contract["states"][0]
        state["baselines"] = []
        self.invalid("baseline_gap reason")
        state["baseline_gap"] = "  "
        self.invalid("baseline_gap reason")

    def test_baseline_and_gap_cannot_coexist(self):
        self.contract["states"][0]["baseline_gap"] = "Missing fixture"
        self.invalid("cannot have baselines and a baseline_gap")

    def test_declared_gap_is_visible_and_does_not_grant_final_approval(self):
        self.contract["states"][0].update(baselines=[], baseline_gap="Missing fixture")
        e = self.ready()
        packet = e.review_packet()
        self.assertEqual(packet["baseline_gaps"], [
            {"state_id": "editor", "capability_id": "dynamics", "reason": "Missing fixture"}])
        self.assertEqual(e.status()["state_coverage"][0]["baseline_gap"], "Missing fixture")
        _, result = e.validate_result(e.tasks["visual"], {"points": [
            {"acceptance_id": "editor-visual", "status": "blocked", "reason": "Missing fixture"}]})
        self.assertFalse(result["accepted"])
        with self.assertRaisesRegex(ValueError, "Every required point"):
            approval_demo(e, "final", "premature-gap-final")

    def test_declared_gap_rejects_checked_visual_observation(self):
        self.contract["states"][0].update(baselines=[], baseline_gap="Missing fixture")
        e = self.ready()
        with self.assertRaisesRegex(ValueError, "Visual point has no baseline"):
            e.validate_result(e.tasks["visual"], {"points": [
                {"acceptance_id": "editor-visual", "status": "checked", "observations": [
                    {"assertion_id": "observed", "actual": True}]}]})

    def test_resolving_baseline_gap_reopens_affected_approval(self):
        baselines = copy.deepcopy(self.contract["states"][0]["baselines"])
        self.contract["states"][0].update(baselines=[], baseline_gap="Missing fixture")
        e = self.ready()
        approved_digest = e.joint_status("dynamics")["input_digest"]
        self.contract["states"][0]["baselines"] = baselines
        self.contract["states"][0].pop("baseline_gap")
        revised = self.root / "resolved-gap.json"
        runtime.write_json(revised, self.contract)
        e.revise("resolve-gap", revised, "Captured missing fixture")
        self.assertNotEqual(e.joint_status("dynamics")["input_digest"], approved_digest)
        self.assertNotEqual(e.joint_status("dynamics")["status"], "approved")
        self.assertEqual(e.verify()["status"], "incomplete")

    def test_changed_source_invalidates_device_and_final(self):
        e = self.complete()
        source = self.root / "harmony/dynamics.py"
        source.write_text(source.read_text()+"\n# changed\n")
        self.assertEqual(self.rows()["implement"]["status"], "agent_required")
        self.assertEqual(self.rows()["device"]["status"], "blocked")
        self.assertEqual(e.verify()["status"], "incomplete")

    def test_corrupt_shared_capture_invalidates_device_and_visual(self):
        e = self.complete()
        shot = next(r for r in e.receipt("visual")["evidence"] if r["path"].endswith(".png"))
        Path(shot["path"]).write_bytes(b"changed")
        self.assertEqual(self.rows()["visual"]["status"], "blocked")
        self.assertEqual(self.rows()["device"]["status"], "stale")
        self.assertEqual(e.status()["coverage"]["passed"], 1)

    def test_visual_reuses_current_device_capture_through_dependency(self):
        e = self.execute_suite()
        device = {r["path"] for r in e.receipt("device")["evidence"] if r["path"].endswith(".png")}
        visual = {r["path"] for r in e.receipt("visual")["evidence"] if r["path"].endswith(".png")}
        self.assertEqual(device, visual)
        self.assertTrue(device)

    def test_supplemental_evidence_reopens_final_review(self):
        e = self.complete()
        note = e.root / "extra.txt"
        note.write_text("Additional visual observation\n")
        e.attach("supplement", "visual", [str(note)])
        self.assertEqual(self.rows()["visual"]["status"], "passed")
        self.assertEqual(self.rows()["review"]["status"], "agent_required")
        self.assertEqual(e.verify()["status"], "incomplete")

    def test_stale_approval_cannot_be_recorded(self):
        e = self.init()
        record_demo(e, "discover", "discover")
        note = e.root / "approval.txt"
        note.write_text("Synthetic review only\n")
        with self.assertRaisesRegex(ValueError, "Stale joint"):
            e.approve("stale", {"gate": "joint", "actor_type": "demo", "reviewer": "example", "summary": "Synthetic",
                "evidence": [str(note)], "reviews": [{"capability_id": "dynamics", "input_digest": "old",
                    "decisions": {d: "approved" for d in ("requirements", "technical", "ui")}}]})

    def test_real_run_cannot_use_synthetic_approval(self):
        self.contract["demo"] = False
        visual = next(t for t in self.contract["tasks"] if t["kind"] == "visual")
        visual.pop("command")
        visual["visual_comparison"] = {aid: {"assertion_id": "observed", "capture_from": {
            "task_id": "device", "acceptance_id": aid.replace("-visual", "-device")}}
            for aid in visual["acceptance_ids"]}
        e = self.init()
        record_demo(e, "discover", "discover")
        with self.assertRaisesRegex(ValueError, "human"):
            approval_demo(e)

    def test_rejected_ui_blocks_implementation(self):
        e = self.init()
        record_demo(e, "discover", "discover")
        note = e.root / "approval.txt"
        note.write_text("Synthetic rejection\n")
        result = {"gate": "joint", "actor_type": "demo", "reviewer": "example", "summary": "UI needs changes",
            "evidence": [str(note)], "reviews": [{"capability_id": "dynamics",
            "input_digest": e.status()["joint_review"][0]["input_digest"],
            "decisions": {"requirements": "approved", "technical": "approved", "ui": "rejected"}}]}
        e.approve("reject", result)
        self.assertEqual(e.status()["joint_review"][0]["status"], "rejected")
        self.assertEqual(self.rows()["implement"]["status"], "awaiting_human")
        self.assertTrue(e.approve("reject", result)["replayed"])

    def test_targeted_run_includes_dependencies_and_leaves_other_tests(self):
        e = self.ready()
        result = e.run("targeted", task_ids=["visual"])
        self.assertEqual(result["executed"], ["build", "device", "visual"])
        self.assertEqual(self.rows()["contracts"]["status"], "ready")
        self.assertEqual(self.rows()["device"]["status"], "passed")
        self.assertEqual(e.run("rest")["executed"], ["contracts"])

    def test_cheap_checks_first_and_metrics_are_measured(self):
        e = self.ready()
        result = e.run("batch")
        self.assertEqual(result["executed"], ["contracts", "build", "device", "visual"])
        metrics = result["metrics"]
        self.assertEqual(metrics["executor_attempts"], 4)
        self.assertGreater(metrics["executor_seconds"], 0)
        self.assertGreater(metrics["adapter_reported_phase_seconds"]["assert"], 0)
        self.assertEqual(set(metrics["executor_by_kind"]), {"discover", "implement", "contract", "build", "device", "visual"})

    def test_interruption_needs_reconciliation(self):
        e = self.ready()
        with patch("engine.runtime.run_process", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                e.run("interrupted", limit=1)
        e = Harness(e.root)
        self.assertEqual(self.rows(e)["contracts"]["status"], "in_doubt")
        self.assertEqual(e.run("interrupted", limit=1)["operation_status"], "started")
        note = e.root / "reconcile.txt"
        note.write_text("Confirmed no running child and no business effects\n")
        e.retry("reconcile", "contracts", "Confirmed local interruption", [str(note)])
        self.assertIn("contracts", e.run("resume")["executed"])

    def test_same_operation_is_not_executed_twice(self):
        e = self.ready()
        e.run("batch")
        count = e.metrics()["executor_attempts"]
        self.assertTrue(e.run("batch")["replayed"])
        self.assertEqual(e.metrics()["executor_attempts"], count)
        with self.assertRaisesRegex(ValueError, "different input"):
            e.run("batch", task_ids=["visual"])

    def test_timer_measures_recorded_interval_and_replays(self):
        e = self.init()
        with patch("engine.time.time", return_value=100):
            e.timer("start", "analysis", "discovery", "start")
        with patch("engine.time.time", return_value=105):
            metrics = e.timer("stop", "analysis", "discovery", "stop")
        self.assertEqual(metrics["agent_wall_spans"][0]["elapsed_seconds"], 5)
        self.assertTrue(e.timer("stop", "analysis", "discovery", "stop")["replayed"])

    def test_add_state_invalidates_approval_without_removing_old_points(self):
        e = self.complete()
        new = copy.deepcopy(self.contract)
        state = copy.deepcopy(new["states"][1])
        state["id"] = "upload-error"
        state["kind"] = "error"
        new["states"].append(state)
        new["review_scopes"][0]["state_ids"].append(state["id"])
        for kind in ("device", "visual"):
            point = copy.deepcopy(next(a for a in new["acceptance"] if a["kind"] == kind))
            point.update(id=state["id"]+"-"+kind, state_id=state["id"])
            new["acceptance"].append(point)
            next(t for t in new["tasks"] if t["kind"] == kind)["acceptance_ids"].append(point["id"])
        path = self.root / "revision.json"
        runtime.write_json(path, new)
        e.revise("expand", path, "Discovered upload error state")
        self.assertEqual(e.status()["joint_review"][0]["status"], "stale")
        self.assertEqual(e.status()["coverage"]["required"], 7)
        self.assertEqual(self.rows()["visual"]["attempts"], 1)

    def test_revision_cannot_remove_state_or_change_expected(self):
        e = self.init()
        changed = copy.deepcopy(self.contract)
        changed["states"][0]["expected"] = "weaker behavior"
        path = self.root / "revision.json"
        runtime.write_json(path, changed)
        with self.assertRaisesRegex(ValueError, "existing states"):
            e.revise("weaken", path, "Attempt weakening")

    def test_project_context_is_read_only_and_lists_tools(self):
        e = self.init()
        before = (e.root / "harness-state.json").read_bytes()
        context = e.project_context()
        self.assertTrue(context["tools"][0]["available"])
        self.assertEqual(context["modules"][0]["id"], "demo")
        self.assertEqual((e.root / "harness-state.json").read_bytes(), before)

    def test_removed_format_is_rejected(self):
        self.contract["format"] = "autonomous-migration-experiment/1"
        self.invalid("Expected Migration Harness")

    def test_cli_demo_verify_never_returns_success(self):
        e = self.complete()
        result = subprocess.run([sys.executable, str(ROOT / "migration.py"), "verify", "--run-dir", str(e.root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "demo_only")


if __name__ == "__main__":
    unittest.main()
