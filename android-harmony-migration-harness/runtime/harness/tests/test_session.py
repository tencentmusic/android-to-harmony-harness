import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import test_engine as fixtures
from demo import record_demo
from engine import Harness, runtime, read_json
from review_html import write_review_packet
from session import record, recovery, end_check, load, metrics


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ReviewedHarnessTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.note = self.case.root / "tools/request.txt"
        self.note.write_text("Human request and observed external dependency")

    def start(self, e, scope=None):
        return record(e.root, "session-start", {"action": "start", "session_id": "host-1",
            "request": "Execute the authorized request", "scope": scope or {"mode": "full"},
            "evidence": [str(self.note)]}, e)

    def end(self, e, reason, op="stop", **kw):
        return end_check(e, op, {"session_id": "host-1", "reason": reason, **kw})

    def test_independent_work_vetoes_stop_and_hook_exits_nonzero(self):
        e = self.case.ready()
        self.start(e)
        value = self.end(e, "waiting_human")
        self.assertFalse(value["allowed"])
        self.assertEqual(value["next_action"]["owner"], "agent")
        path = e.root / "end.json"
        runtime.write_json(path, {"session_id": "host-1", "reason": "waiting_human"})
        result = subprocess.run([sys.executable, str(ROOT / "host_hook.py"), "--run-dir", str(e.root),
            "--operation-id", "hook", "--input", str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(json.loads(result.stdout)["automatic_host_hook"])
        self.assertEqual(metrics(e.root)["denied_end_checks"], 2)

    def test_review_only_can_finish_preparation_without_quality_approval(self):
        e = self.case.init()
        self.start(e, {"mode": "review", "capability_ids": ["dynamics"]})
        record_demo(e, "discover", "discover")
        self.assertFalse(self.end(e, "request_complete", "no-html")["allowed"])
        write_review_packet(e)
        result = self.end(e, "request_complete")
        self.assertTrue(result["allowed"])
        self.assertFalse(result["quality_gate_granted"])
        self.assertEqual(e.joint_status("dynamics")["status"], "awaiting_human")
        self.assertEqual(recovery(e)["state"], "request_complete")

    def test_scoped_task_completion_and_stale_receipt(self):
        e = self.case.ready()
        self.start(e, {"mode": "tasks", "task_ids": ["contracts"]})
        e.run("one-task", task_ids=["contracts"])
        self.assertTrue(self.end(e, "request_complete")["allowed"])
        self.assertTrue(self.end(e, "request_complete")["replayed"])
        (self.case.root / "harmony/dynamics.py").write_text("changed input")
        self.assertFalse(self.end(e, "request_complete")["allowed"])
        self.assertEqual(recovery(e)["state"], "working")

    def test_pause_requires_human_instruction_and_evidence(self):
        e = self.case.ready()
        self.start(e)
        with self.assertRaisesRegex(ValueError, "human instruction"):
            self.end(e, "user_paused")
        self.assertTrue(self.end(e, "user_paused", actor_type="human", user_instruction="Pause now",
                                 evidence=[str(self.note)])["allowed"])
        self.assertEqual(recovery(e)["state"], "paused")

    def test_resume_invalidates_old_pause_receipt_without_changing_task_inputs(self):
        e = self.case.ready()
        self.start(e)
        pause = {"actor_type": "human", "user_instruction": "Pause now", "evidence": [str(self.note)]}
        self.assertTrue(self.end(e, "user_paused", **pause)["allowed"])
        self.assertTrue(self.end(e, "user_paused", **pause)["allowed"])
        record(e.root, "resume", {"action": "resume", "session_id": "host-1",
                                 "summary": "User resumed the request"}, e)
        stale = self.end(e, "user_paused", **pause)
        self.assertTrue(stale["replayed"])
        self.assertFalse(stale["allowed"])
        self.assertFalse(stale["current"])
        self.assertEqual(recovery(e)["state"], "working")
        self.assertTrue(self.end(e, "user_paused", "new-pause", **pause)["allowed"])

    def test_resume_invalidates_completed_receipt_even_when_task_inputs_are_unchanged(self):
        e = self.case.ready()
        self.start(e, {"mode": "tasks", "task_ids": ["contracts"]})
        e.run("contracts", task_ids=["contracts"])
        self.assertTrue(self.end(e, "request_complete")["allowed"])
        record(e.root, "resume", {"action": "resume", "session_id": "host-1", "summary": "Recheck request"}, e)
        self.assertFalse(self.end(e, "request_complete")["allowed"])
        self.assertNotEqual(recovery(e)["state"], "request_complete")
        self.assertTrue(self.end(e, "request_complete", "current-end")["allowed"])

    def test_holds_are_evidence_bound_and_do_not_hide_other_work(self):
        e = self.case.ready()
        self.start(e)
        record(e.root, "hold-build", {"action": "hold", "session_id": "host-1", "id": "offline",
            "task_ids": ["build"], "reason": "Required signing service is unavailable",
            "resume_condition": "Signing probe passes", "expires_at": time.time() + 100,
            "evidence": [str(self.note)]}, e)
        self.assertFalse(self.end(e, "waiting_external", "still-independent")["allowed"])
        e.run("contracts", task_ids=["contracts"])
        self.assertTrue(self.end(e, "waiting_external")["allowed"])
        with patch("session.time.time", return_value=time.time() + 101):
            self.assertFalse(self.end(e, "waiting_external")["allowed"])
            self.assertEqual(len(recovery(e)["invalid_holds"]), 1)

    def test_recovery_is_read_only_and_reconciles_completed_attempt(self):
        e = self.case.ready()
        self.start(e)
        runner = runtime.run_process
        def interrupted(*args, **kwargs):
            runner(*args, **kwargs)
            raise KeyboardInterrupt("Lost host after completed command")
        with patch("engine.runtime.run_process", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                e.run("interrupted", task_ids=["build"])
        e = Harness(e.root)
        before = {name: (e.root / name).read_bytes() for name in ("harness-state.json", "agent-session.json")}
        result = recovery(e)
        self.assertEqual(result["state"], "interrupted")
        self.assertEqual(result["active_attempts"][0]["action"], "reconcile_completed_result")
        self.assertFalse(result["active_attempts"][0]["replay_allowed"])
        self.assertEqual(before, {name: (e.root / name).read_bytes() for name in before})
        e.reconcile("recover", "build", "Observed completed command", [str(self.note)], "accept")
        self.assertEqual({r["id"]: r["status"] for r in e.status()["tasks"]}["build"], "passed")
        self.assertEqual(len(list((e.root / "attempts").glob("build-*/process.json"))), 1)
        self.assertIn("checkpoint", load(e.root)["sessions"]["host-1"])

    def test_pre_run_journal_attach_preserves_preparation_and_blocks_original(self):
        e = self.case.init()
        journal = self.case.root / "preparation"
        record(journal, "start", {"action": "start", "session_id": "host-1", "request": "Prepare review",
                                 "scope": {"mode": "full"}})
        record(e.root, "attach", {"action": "attach", "journal": str(journal)}, e)
        self.assertEqual(load(e.root)["run_id"], e.state["run_id"])
        self.assertEqual(load(e.root)["events"][0]["kind"], "start")
        with self.assertRaisesRegex(ValueError, "attached run"):
            record(journal, "later", {"action": "phase"})

    def test_torn_attach_can_only_resume_into_reserved_run(self):
        e = self.case.init()
        journal = self.case.root / "preparation"
        record(journal, "start", {"action": "start", "session_id": "host-1", "request": "Prepare review",
                                 "scope": {"mode": "full"}})
        writer = runtime.write_json
        def fail_destination(path, value):
            if Path(path) == e.root / "agent-session.json":
                raise OSError("Destination unavailable")
            return writer(path, value)
        with patch("session.runtime.write_json", side_effect=fail_destination):
            with self.assertRaises(OSError):
                record(e.root, "attach", {"action": "attach", "journal": str(journal)}, e)
        record(e.root, "attach", {"action": "attach", "journal": str(journal)}, e)
        self.assertEqual(load(e.root)["run_id"], e.state["run_id"])

    def test_repeated_attach_preserves_pause_and_all_subsequent_events(self):
        e = self.case.ready()
        journal = self.case.root / "preparation"
        record(journal, "start", {"action": "start", "session_id": "host-1", "request": "Work",
                                 "scope": {"mode": "full"}})
        attach = {"action": "attach", "journal": str(journal)}
        record(e.root, "attach", attach, e)
        self.assertTrue(self.end(e, "user_paused", actor_type="human", user_instruction="Pause now",
                                 evidence=[str(self.note)])["allowed"])
        before = (e.root / "agent-session.json").read_bytes()
        self.assertTrue(record(e.root, "attach", attach, e)["replayed"])
        with self.assertRaisesRegex(ValueError, "fresh run session"):
            record(e.root, "attach-again", attach, e)
        self.assertEqual((e.root / "agent-session.json").read_bytes(), before)
        self.assertEqual(recovery(e)["state"], "paused")

    def test_attach_destination_is_complete_when_write_succeeds_before_reporting_failure(self):
        e = self.case.init()
        journal = self.case.root / "preparation"
        record(journal, "start", {"action": "start", "session_id": "host-1", "request": "Work",
                                 "scope": {"mode": "full"}})
        attach = {"action": "attach", "journal": str(journal)}
        writer = runtime.write_json
        def fail_after_commit(path, value):
            writer(path, value)
            if Path(path) == e.root / "agent-session.json":
                raise OSError("Destination committed before host interruption")
        with patch("session.runtime.write_json", side_effect=fail_after_commit):
            with self.assertRaises(OSError):
                record(e.root, "attach", attach, e)
        record(e.root, "phase", {"action": "phase", "session_id": "host-1", "phase": "diagnosis",
                                 "summary": "Inspect original attach"}, e)
        before = (e.root / "agent-session.json").read_bytes()
        self.assertTrue(record(e.root, "attach", attach, e)["replayed"])
        self.assertEqual((e.root / "agent-session.json").read_bytes(), before)

    def test_telemetry_corruption_keeps_core_diagnosable_but_cannot_authorize_end(self):
        e = self.case.ready()
        self.start(e)
        (e.root / "agent-session.json").write_text("incomplete JSON")
        e.run("contract-only", task_ids=["contracts"])
        rows = {r["id"]: r for r in e.status()["tasks"]}
        self.assertEqual(rows["contracts"]["status"], "passed")
        self.assertEqual(recovery(e)["next_action"]["action"], "repair_session_journal")
        self.assertEqual(e.metrics()["session"]["status"], "invalid_journal")
        with self.assertRaises(ValueError):
            self.end(e, "complete")

    def test_session_intervals_do_not_add_parent_execution_seconds(self):
        journal = self.case.root / "timing"
        with patch("session.time.time", return_value=100):
            record(journal, "start", {"action": "start", "session_id": "host-1", "request": "Work", "scope": {"mode": "full"}})
        for at, action, op in ((110, "phase", "implement"), (130, "interrupt", "lost"), (140, "resume", "back")):
            with patch("session.time.time", return_value=at):
                record(journal, op, {"action": action, "session_id": "host-1", "phase": "implementation", "summary": op})
        with patch("session.time.time", return_value=150):
            totals = metrics(journal)
        self.assertEqual(totals["seconds_by_state_or_phase"], {"discovery": 10, "implementation": 30, "interrupted": 10})
        self.assertEqual(totals["interruptions"], 1)


if __name__ == "__main__":
    unittest.main()
