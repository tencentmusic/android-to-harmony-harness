import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_engine as fixtures
from test_engine import ROOT, Harness, record_demo, approval_demo
from continuation import plan
from report import markdown
from handoff import write_handoff
from test_handoff import example


def snapshot(rows, gates=None, final="blocked", overall="incomplete"):
    return {"status": overall, "tasks": rows, "joint_review": gates or [],
            "final_review": {"status": final}, "points": [
                {"acceptance_id": row["id"] + "-point", "task_id": row["id"],
                 "status": "passed" if row["status"] == "passed" else "not_checked"} for row in rows]}


class ContinuationPlanTests(unittest.TestCase):
    def test_partial_failure_and_blocked_descendants_require_diagnosis(self):
        value = snapshot([{"id":"contracts", "status":"needs_diagnosis"},
                          {"id":"device", "status":"blocked", "dependencies":["contracts"]},
                          {"id":"visual", "status":"blocked", "dependencies":["device"]}])
        result = plan(value)
        self.assertFalse(result["can_end_turn"])
        self.assertEqual(result["next_action"]["action"], "diagnose_task")
        self.assertEqual(result["next_action"]["pending_acceptance_ids"], ["contracts-point"])
        self.assertEqual(result["blocked_tasks"][-1]["root_task_ids"], ["contracts"])
        self.assertFalse(result["blocked_tasks"][-1]["external_block_confirmed"])

    def test_all_repairable_frontier_states_require_agent_work(self):
        cases = {"ready":"run_task", "stale":"run_task", "agent_required":"complete_agent_task",
                 "input_missing":"repair_inputs", "budget_exhausted":"diagnose_budget",
                 "in_doubt":"inspect_interrupted_attempt", "unknown":"diagnose_task_state",
                 "blocked":"diagnose_task_state"}
        for state, action in cases.items():
            with self.subTest(state=state):
                result = plan(snapshot([{"id":"work", "status":state}]))
                self.assertFalse(result["can_end_turn"])
                self.assertEqual(result["next_action"]["action"], action)
                self.assertFalse(result["replay_saved_commands"])
                self.assertFalse(result["background_worker"])

    def test_external_sounding_error_is_not_an_external_hold(self):
        result = plan(snapshot([{"id":"device", "status":"needs_diagnosis", "reason":"Device unavailable; user required"}]))
        self.assertFalse(result["can_end_turn"])
        self.assertEqual(result["next_action"]["owner"], "agent")

    def test_independent_work_precedes_human_gate(self):
        value = snapshot([{"id":"implement-a", "status":"awaiting_human", "review_scopes":["a"]},
                          {"id":"discover-b", "status":"agent_required"}],
                         [{"capability_id":"a", "status":"awaiting_human"}])
        result = plan(value, ready_review_scopes={"a"})
        self.assertFalse(result["can_end_turn"])
        self.assertEqual(result["next_action"]["task_id"], "discover-b")
        self.assertEqual(result["pending_actions"][-1]["owner"], "user")

    def test_only_human_gate_allows_review_request_not_completion(self):
        result = plan(snapshot([{"id":"implement", "status":"awaiting_human", "review_scopes":["a"]},
                                {"id":"build", "status":"blocked", "dependencies":["implement"]}],
                               [{"capability_id":"a", "status":"awaiting_human"}]), ready_review_scopes={"a"})
        self.assertTrue(result["can_end_turn"])
        self.assertEqual(result["status"], "awaiting_human")
        self.assertEqual(result["next_action"]["command"], "review-packet")

    def test_invalidated_or_rejected_materials_need_preparation(self):
        for gate in ["stale", "rejected"]:
            result = plan(snapshot([{"id":"implement", "status":"awaiting_human", "review_scopes":["a"]}],
                                   [{"capability_id":"a", "status":gate}]))
            self.assertFalse(result["can_end_turn"])
            self.assertEqual(result["next_action"]["action"], "repair_joint_review")

    def test_valid_approvals_are_not_requested_again(self):
        result = plan(snapshot([{"id":"implement", "status":"agent_required"}],
                               [{"capability_id":"a", "status":"approved"}]))
        self.assertFalse(any(a["owner"] == "user" for a in result["pending_actions"]))

    def test_final_review_and_demo_are_distinct_from_real_completion(self):
        rows = [{"id":"review", "status":"passed"}]
        waiting = plan(snapshot(rows, final="awaiting_human"), final_report_ready=True)
        self.assertTrue(waiting["can_end_turn"])
        self.assertEqual(waiting["next_action"]["action"], "final_review")
        for overall, expected in [("demo_only","demo_complete"), ("delivery_ready","complete")]:
            result = plan(snapshot(rows, final="approved", overall=overall), final_report_ready=True)
            self.assertEqual(result["status"], expected)
            self.assertIsNone(result["next_action"])

    def test_inconsistent_completion_fails_closed(self):
        value = snapshot([{"id":"review", "status":"passed"}], final="approved", overall="delivery_ready")
        value["points"][0]["status"] = "not_checked"
        self.assertFalse(plan(value)["can_end_turn"])
        self.assertFalse(plan(snapshot([]))["can_end_turn"])

    def test_human_gate_without_review_material_is_agent_work(self):
        value = snapshot([{"id":"implement", "status":"awaiting_human", "review_scopes":["a"]}],
                         [{"capability_id":"a", "status":"awaiting_human"}])
        self.assertEqual(plan(value)["next_action"]["action"], "prepare_joint_review")
        final = plan(snapshot([{"id":"review", "status":"passed"}], final="awaiting_human"))
        self.assertFalse(final["can_end_turn"])
        self.assertEqual(final["next_action"]["action"], "prepare_developer_handoff")

    def test_pure_projection_preserves_inputs_and_valid_points(self):
        value = snapshot([{"id":"build", "status":"passed"}, {"id":"contracts", "status":"needs_diagnosis"}])
        before = copy.deepcopy(value)
        result = plan(value)
        self.assertEqual(value, before)
        self.assertEqual(result["reusable_task_ids"], ["build"])
        self.assertEqual(result["passed_acceptance_ids"], ["build-point"])


class ContinuationCliTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ReviewedHarnessTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def cli(self, e, command, *args):
        process = subprocess.run([sys.executable, str(ROOT / "migration.py"), command,
                                  "--run-dir", str(e.root), *args], capture_output=True, text=True)
        return process, json.loads(process.stdout or process.stderr)

    def test_status_coverage_inspect_report_and_verify_all_expose_next_action(self):
        (self.case.root / "tools/settings.json").write_text('{"omit_popup":true}')
        e = self.case.execute_suite()
        state = (e.root / "harness-state.json").read_bytes()
        digests = {tid:e.fingerprint(task) for tid,task in e.tasks.items()}
        for command, args in [("status",()), ("coverage",()), ("inspect",("--task","visual")),
                              ("report",()), ("verify",())]:
            process, value = self.cli(e, command, *args)
            self.assertEqual(process.returncode, 1 if command == "verify" else 0, process.stderr)
            self.assertEqual(value["continuation"]["status"], "must_continue")
            self.assertEqual(value["continuation"]["next_action"]["task_id"], "visual")
        self.assertEqual((e.root / "harness-state.json").read_bytes(), state)
        self.assertEqual({tid:e.fingerprint(task) for tid,task in e.tasks.items()}, digests)
        self.assertIn("can_end_turn=false", markdown(e))

    def test_batch_limit_is_not_feature_completion(self):
        e = self.case.ready()
        process, value = self.cli(e, "run", "--operation-id", "one", "--limit", "1")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(len(value["executed"]), 1)
        self.assertFalse(value["continuation"]["can_end_turn"])
        self.assertEqual(value["continuation"]["scope"], "whole_feature")

    def test_selected_task_success_does_not_hide_remaining_work(self):
        e = self.case.ready()
        _, value = self.cli(e, "run", "--operation-id", "build-only", "--tasks", "build")
        self.assertIn("build", value["continuation"]["reusable_task_ids"])
        self.assertFalse(value["continuation"]["can_end_turn"])

    def test_operation_replay_reports_current_advice_without_reexecution(self):
        e = self.case.ready()
        self.cli(e, "run", "--operation-id", "first", "--limit", "1")
        e = Harness(e.root)
        e.run("remaining")
        before = (e.root / "harness-state.json").read_bytes()
        _, value = self.cli(e, "run", "--operation-id", "first", "--limit", "1")
        self.assertTrue(value["replayed"])
        self.assertEqual(value["continuation"]["next_action"]["task_id"], "review")
        self.assertEqual((e.root / "harness-state.json").read_bytes(), before)

    def test_command_error_requires_diagnosis_not_user_handoff_or_replay(self):
        e = self.case.ready()
        process, value = self.cli(e, "inspect", "--task", "unknown")
        self.assertEqual(process.returncode, 2)
        self.assertFalse(value["continuation"]["can_end_turn"])
        self.assertFalse(value["continuation"]["replay_saved_commands"])

    def test_joint_review_wait_requires_current_valid_html_archive(self):
        e = self.case.init()
        record_demo(e, "discover", "discover")
        _, first = self.cli(e, "status")
        self.assertFalse(first["continuation"]["can_end_turn"])
        _, packet = self.cli(e, "review-packet")
        self.assertTrue(packet["continuation"]["can_end_turn"])
        Path(packet["html_path"]).unlink()
        _, missing = self.cli(e, "status")
        self.assertEqual(missing["continuation"]["next_action"]["action"], "prepare_joint_review")

    def test_changed_materials_require_new_packet_then_user_review(self):
        e = self.case.ready()
        (self.case.root / "tools/ui.md").write_text("Updated reviewed material")
        _, first = self.cli(e, "status")
        self.assertEqual(first["continuation"]["next_action"]["action"], "repair_joint_review")
        _, packet = self.cli(e, "review-packet")
        self.assertEqual(packet["continuation"]["status"], "awaiting_human")
        archived = Path(packet["html_path"]).parent / "dynamics/snapshot.json"
        data = json.loads(archived.read_text())
        Path(data["files"][0]["archived"]["path"]).write_text("Corrupted archive")
        _, corrupted = self.cli(e, "status")
        self.assertFalse(corrupted["continuation"]["can_end_turn"])

    def test_final_review_requires_report_bound_to_current_evidence(self):
        e = self.case.execute_suite()
        record_demo(e, "review", "review")
        _, pending = self.cli(e, "status")
        self.assertEqual(pending["continuation"]["next_action"]["action"], "prepare_developer_handoff")
        _, reported = self.cli(e, "report")
        self.assertFalse(reported["continuation"]["can_end_turn"])
        handoff = write_handoff(e, example())
        _, ready = self.cli(e, "status")
        self.assertEqual(ready["continuation"]["status"], "awaiting_human")
        Path(handoff["html_path"]).write_text("Stale or incomplete report")
        _, changed = self.cli(e, "status")
        self.assertFalse(changed["continuation"]["can_end_turn"])

    def test_projection_changes_do_not_invalidate_package_or_approval_fingerprints(self):
        e = self.case.execute_suite()
        before = {tid:e.fingerprint(task) for tid,task in e.tasks.items()}
        approval = e.joint_status("dynamics")
        # Presentation/CLI code stays outside execution evidence fingerprints.
        paths = {item["path"] for task in e.tasks.values() for item in e.input_snapshot(task)["engine_files"]}
        self.assertNotIn(str(ROOT / "continuation.py"), paths)
        with patch("continuation.plan", return_value={"can_end_turn":False}):
            after = {tid:e.fingerprint(task) for tid,task in e.tasks.items()}
        self.assertEqual(after, before)
        self.assertEqual(e.joint_status("dynamics"), approval)

    def test_complete_demo_still_cannot_pass_real_verify(self):
        e = self.case.complete()
        write_handoff(e, example())
        process, value = self.cli(e, "verify")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(value["continuation"]["status"], "demo_complete")


if __name__ == "__main__":
    unittest.main()
