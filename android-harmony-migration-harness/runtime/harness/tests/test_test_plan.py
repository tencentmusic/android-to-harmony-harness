import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))
from contract import validate_contract
from demo import prepare, record_demo, approval_demo
from engine import Harness, read_json, runtime
from report import markdown
from review_html import write_review_packet
from test_plan import compile_contract, scope_plan


def example_plan():
    return {"version": 1, "changes": [{"id": "data-source", "capability_id": "dynamics",
        "before": "Embedded values", "after": "Load values through the migrated API",
        "sources": [{"root": "tools", "path": "project.md", "locator": "Synthetic change analysis"}]}],
        "scenarios": [
            {"id": "values", "capability_id": "dynamics", "change_ids": ["data-source"],
             "state_ids": ["editor"], "behavior": "Preserve wire values", "risk": "Wrong values change requests",
             "grouping_reason": "One shared value provider", "observable_ui": False,
             "verifications": [{"kind": "contract", "task_id": "contracts", "reason": "Deterministic API result",
                                "assertions": [{"id": "gift-values", "operator": "equal", "expected": [1, 10, 100]}]}]},
            {"id": "flow", "capability_id": "dynamics", "change_ids": ["data-source"],
             "state_ids": ["editor", "photo-source"], "behavior": "Editor and picker remain usable",
             "risk": "Integration can lose loaded state", "grouping_reason": "One continuous editor-to-picker flow",
             "observable_ui": True, "verifications": [{"kind": "device", "task_id": "device",
                 "reason": "Check native integration once for both states",
                 "assertions": [{"id": "observed", "operator": "truthy", "expected": True}]}]}],
        "excluded_states": []}


class TestPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "case"
        self.path = prepare(self.root)
        self.raw = read_json(self.path)
        self.plan = example_plan()

    def compile(self, plan=None):
        return validate_contract(compile_contract(self.raw, plan or self.plan), self.path.parent)

    def init(self):
        runtime.write_json(self.path, self.compile())
        return Harness.init(self.path, self.root / "run")

    def run_suite(self):
        e = self.init()
        record_demo(e, "discover", "discover")
        approval_demo(e)
        record_demo(e, "implement", "implement")
        e.run("batch")
        return e

    def revision(self, e, plan, replacements=(), retirements=()):
        plan["revision"] = {"previous_digest": runtime.digest(e.contract["test_plan"]),
                            "reason": "Reconcile scope against implementation diff",
                            "replacements": list(replacements), "retirements": list(retirements)}
        path = self.root / "revised.json"
        runtime.write_json(path, self.compile(plan))
        return path

    def retirement(self):
        return {"acceptance_id": "values-contract", "capability_id": "dynamics", "reason": "Value provider did not change in final diff",
                "sources": [{"root": "tools", "path": "project.md", "locator": "Synthetic final diff"}]}

    def test_background_and_grouped_flow_do_not_expand_to_three_methods(self):
        for state in self.raw["states"]:
            state["baselines"] = []
        contract = self.compile()
        self.assertEqual([a["id"] for a in contract["acceptance"]], ["values-contract", "flow-device"])
        self.assertNotIn("visual", [t["id"] for t in contract["tasks"]])
        review = next(t for t in contract["tasks"] if t["kind"] == "review")
        self.assertEqual(set(review["depends_on"]), {"contracts", "device"})
        self.assertTrue(contract["capabilities"][0]["has_ui"])
        e = self.init()
        self.assertEqual(e.review_packet()["baseline_gaps"], [])
        self.assertEqual(len(e.status()["state_coverage"][1]["points"]), 1)

    def test_selected_plan_executes_and_reaches_final_gate_with_grouped_coverage(self):
        e = self.run_suite()
        status = e.status()
        self.assertEqual(status["coverage"]["passed"], 2)
        self.assertEqual(status["test_plan"]["scenarios"], 2)
        self.assertEqual(status["test_plan"]["execution_suites"], 2)
        self.assertEqual(status["test_plan"]["scenario_results"][1]["status"], "passed")
        requests = list((e.root / "attempts").glob("device-*/request.json"))
        request = read_json(requests[0])
        self.assertEqual({s["id"] for s in request["states"]}, {"editor", "photo-source"})
        record_demo(e, "review", "review")
        approval_demo(e, "final", "final")
        self.assertEqual(e.verify()["status"], "demo_only")
        self.assertIn("执行套件 2", markdown(e))

    def test_visual_is_opt_in_and_all_grouped_states_need_baselines(self):
        self.plan["scenarios"][1]["verifications"].append({"kind": "visual", "task_id": "visual",
            "reason": "Changed dimensions affect rendering", "assertions": [{"id": "layout", "operator": "truthy", "expected": True}]})
        self.raw["states"][1]["baselines"] = []
        self.raw["states"][1]["baseline_gap"] = "Need picker screenshot"
        e = self.run_suite()
        self.assertNotEqual(e.status()["coverage"]["passed"], 3)
        self.assertEqual(len(e.review_packet()["baseline_gaps"]), 1)
        visual = next(t for t in e.status()["tasks"] if t["id"] == "visual")
        self.assertIn("no baseline", visual["reason"])

    def test_invalid_selection_and_unrelated_points_rejected(self):
        for mutate, error in [
            (lambda p: p["scenarios"][0].update(change_ids=[]), "known changes"),
            (lambda p: p["scenarios"][0].update(change_ids=["unrelated"]), "known changes"),
            (lambda p: p["scenarios"][0]["verifications"][0].update(kind="visual", task_id="visual"), "Background-only"),
            (lambda p: p["scenarios"][1].update(state_ids=["editor"]), "every inventory state"),
            (lambda p: p["scenarios"][0]["verifications"][0].update(reason=""), "selection reason"),
            (lambda p: p["scenarios"][0]["verifications"][0].update(visual_comparision={}), "Unknown verification fields"),
            (lambda p: p["scenarios"][0]["verifications"][0].update(visual_comparison={}), "visual verification binding"),
            (lambda p: p.update(version=True), "version")]:
            with self.subTest(error=error):
                plan = copy.deepcopy(self.plan)
                mutate(plan)
                with self.assertRaisesRegex(ValueError, error):
                    self.compile(plan)
        contract = self.compile()
        contract["acceptance"][0]["assertions"][0]["expected"] = []
        with self.assertRaisesRegex(ValueError, "generated"):
            validate_contract(contract, self.root)

    def test_excluded_inventory_is_visible_and_not_reported_as_passed(self):
        self.plan["scenarios"][1]["state_ids"] = ["editor"]
        self.plan["excluded_states"] = [{"state_id": "photo-source", "capability_id": "dynamics",
            "reason_code": "unchanged", "reason": "Picker implementation is unchanged",
            "sources": [{"root": "tools", "path": "project.md", "locator": "Synthetic diff"}]}]
        e = self.run_suite()
        state = e.status()["state_coverage"][1]
        self.assertEqual(state["selection"], "excluded")
        self.assertEqual(state["points"], [])
        self.assertIn("Picker implementation is unchanged", markdown(e))
        packet = write_review_packet(e)
        html = Path(packet["html_path"]).read_text()
        self.assertIn("Picker implementation is unchanged", html)
        self.assertIn("本次测试计划", html)

    def test_plan_sources_bind_approval_and_execution_inputs(self):
        (self.root / "tools/change.txt").write_text("Old source to new source\n")
        self.plan["changes"][0]["sources"][0]["path"] = "change.txt"
        e = self.init()
        before = e.fingerprint(e.tasks["discover"])
        gate = e.joint_status("dynamics")["input_digest"]
        (self.root / "tools/change.txt").write_text("Changed scope\n")
        self.assertNotEqual(before, e.fingerprint(e.tasks["discover"]))
        self.assertNotEqual(gate, e.joint_status("dynamics")["input_digest"])

    def test_legacy_contract_keeps_original_policy(self):
        contract = copy.deepcopy(self.raw)
        contract["acceptance"] = [a for a in contract["acceptance"] if a["kind"] != "visual"]
        with self.assertRaisesRegex(ValueError, "visual"):
            validate_contract(contract, self.root)
        self.assertNotIn("test_plan", validate_contract(self.raw, self.root))

    def test_plan_cannot_be_added_to_existing_legacy_run(self):
        e = Harness.init(self.path, self.root / "run")
        next_path = self.root / "planned.json"
        runtime.write_json(next_path, self.compile())
        with self.assertRaisesRegex(ValueError, "mode cannot be changed"):
            e.revise("switch", next_path, "Try new mode")

    def test_evidenced_retirement_archives_old_contract_and_reopens_review(self):
        e = self.run_suite()
        plan = copy.deepcopy(self.plan)
        plan["scenarios"] = plan["scenarios"][1:]
        path = self.revision(e, plan, retirements=[self.retirement()])
        e.revise("retire", path, "Final diff excludes old provider")
        self.assertTrue((e.root / "contracts/001.json").exists())
        self.assertEqual(e.state["revision"], 2)
        self.assertNotIn("contracts", e.tasks)
        self.assertEqual(e.metrics()["executor_by_kind"]["contract"]["attempts"], 1)
        self.assertEqual(e.joint_status("dynamics")["status"], "stale")
        self.assertIn("Value provider did not change", markdown(e))

    def test_failed_check_cannot_be_retired_even_when_results_are_stale(self):
        self.plan["scenarios"][0]["verifications"][0]["assertions"][0]["expected"] = [99]
        e = self.run_suite()
        self.assertEqual(e.receipt("contracts")["point_results"][0]["status"], "failed")
        (self.root / "harmony/dynamics.py").write_text("def gift_quantities(): return [1,10,100]\n")
        plan = copy.deepcopy(self.plan)
        plan["scenarios"] = plan["scenarios"][1:]
        path = self.revision(e, plan, retirements=[self.retirement()])
        with self.assertRaisesRegex(ValueError, "failed point cannot be retired"):
            e.revise("retire", path, "Try to hide failure")

    def test_replacement_preserves_assertions_and_requires_new_results(self):
        e = self.run_suite()
        plan = copy.deepcopy(self.plan)
        plan["scenarios"][0]["id"] = "grouped-values"
        replacement = {"acceptance_id": "values-contract", "capability_id": "dynamics", "replacement_ids": ["grouped-values-contract"],
                       "reason": "Group checks through shared provider"}
        path = self.revision(e, plan, replacements=[replacement])
        e.revise("merge", path, "Group equivalent checks")
        self.assertNotIn("grouped-values-contract", e.status()["covered_acceptance_ids"])
        self.assertEqual(e.state["tasks"]["contracts"]["attempts"], 1)

    def test_execution_error_does_not_erase_unresolved_assertion_failure(self):
        self.plan["scenarios"][0]["verifications"][0]["assertions"][0]["expected"] = [99]
        e = self.run_suite()
        (self.root / "harmony/dynamics.py").write_text("raise RuntimeError('fixture execution failed')\n")
        record_demo(e, "implement", "changed-implementation")
        e.run("runtime-error", task_ids=["contracts"])
        self.assertNotIn("receipt", e.state["tasks"]["contracts"])
        self.assertIn("values-contract", e.state["tasks"]["contracts"]["unresolved_failures"])
        plan = copy.deepcopy(self.plan)
        plan["scenarios"] = plan["scenarios"][1:]
        path = self.revision(e, plan, retirements=[self.retirement()])
        with self.assertRaisesRegex(ValueError, "failed point cannot be retired"):
            e.revise("hide", path, "Try to retire after an infrastructure failure")

    def test_replacement_cannot_drop_assertions_or_methods(self):
        e = self.init()
        for field, value in [("assertions", [{"id": "weaker", "operator": "truthy", "expected": True}]),
                             ("kind", "device")]:
            plan = copy.deepcopy(self.plan)
            plan["scenarios"][0]["id"] = "replacement"
            plan["scenarios"][0]["verifications"][0][field] = value
            if field == "kind":
                plan["scenarios"][0]["verifications"][0]["task_id"] = "device"
            target = "replacement-" + plan["scenarios"][0]["verifications"][0]["kind"]
            path = self.revision(e, plan, replacements=[{"acceptance_id": "values-contract", "capability_id": "dynamics",
                "replacement_ids": [target], "reason": "Try weakened replacement"}])
            with self.assertRaisesRegex(ValueError, "weaken"):
                e.revise("weaken-" + field, path, "Try weaker checks")

    def test_replacement_carries_failure_until_successful_retest(self):
        self.plan["scenarios"][0]["verifications"][0]["assertions"][0]["expected"] = [99]
        e = self.run_suite()
        plan = copy.deepcopy(self.plan)
        plan["scenarios"][0]["id"] = "replacement"
        path = self.revision(e, plan, replacements=[{"acceptance_id": "values-contract",
            "capability_id": "dynamics", "replacement_ids": ["replacement-contract"],
            "reason": "Equivalent check with a new ID"}])
        e.revise("replace", path, "Preserve failed check")
        reduced = copy.deepcopy(plan)
        reduced["scenarios"] = reduced["scenarios"][1:]
        retirement = self.retirement()
        retirement["acceptance_id"] = "replacement-contract"
        path = self.revision(e, reduced, retirements=[retirement])
        with self.assertRaisesRegex(ValueError, "failed point cannot be retired"):
            e.revise("hide-replacement", path, "Try to hide failure through replacement")
        (self.root / "harmony/dynamics.py").write_text("def gift_quantities(): return [99]\n")
        record_demo(e, "discover", "rediscover")
        approval_demo(e, operation_id="reapprove-replacement")
        record_demo(e, "implement", "fixed-implementation")
        e.run("retest", task_ids=["contracts"])
        self.assertEqual(e.receipt("contracts")["point_results"][0]["status"], "passed")
        e.revise("retire-after-pass", path, "Verified the fix before narrowing scope")

    def test_cli_generates_new_contract_without_overwriting_input(self):
        plan_path = self.root / "plan.json"
        output = self.root / "planned.json"
        runtime.write_json(plan_path, self.plan)
        command = [sys.executable, str(ROOT / "migration.py"), "plan", "--contract", str(self.path),
                   "--input", str(plan_path), "--output", str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["test_plan"]["scenarios"], 2)
        self.assertEqual(len(read_json(output)["acceptance"]), 2)
        self.assertNotIn("test_plan", read_json(self.path))
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)

    def test_other_capability_revision_does_not_change_scope_snapshot(self):
        before = scope_plan(self.plan, {"dynamics"})
        self.plan["revision"] = {"previous_digest": "old", "reason": "Change another capability",
            "replacements": [], "retirements": [{"capability_id": "other", "acceptance_id": "other-check"}]}
        self.assertEqual(before, scope_plan(self.plan, {"dynamics"}))


if __name__ == "__main__":
    unittest.main()
