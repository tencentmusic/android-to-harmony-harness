import copy
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import test_engine as fixtures
from agent_adapter import FORMAT, inspect_adapter
from agent_runner import drive, reconcile, inspect_control
from engine import Harness, runtime, read_json
from session import record, end_check, recovery, metrics
from demo import record_demo
from review_html import write_review_packet


WORKER = '''import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / "examples"))
from agent_adapter import TURN_FORMAT, EVENT_FORMAT
from engine import Harness
from demo import record_demo
from review_html import write_review_packet
request = json.loads(pathlib.Path(sys.argv[2]).read_text())
output = pathlib.Path(sys.argv[3])
mode = sys.argv[4]
counter = pathlib.Path(request["run_dir"]) / "agent-invocations.txt"
with counter.open("a") as stream:
    stream.write(request["turn_id"] + " " + request["action"] + "\\n")
if mode == "crash":
    raise SystemExit(7)
if mode == "hang":
    import time
    time.sleep(2)
result = {"format": TURN_FORMAT, **{k:request[k] for k in ("turn_id", "run_id", "session_id")}, "status":"completed"}
if mode in ("reconnect", "always-interrupt") and (mode == "always-interrupt" or request["action"] != "resume"):
    result.update(status="interrupted", resume_token={"conversation":"original"})
elif mode in ("work", "reconnect"):
    e = Harness(request["run_dir"])
    action = request["next_action"]
    tid = action.get("task_id")
    if action["action"] == "run_task":
        e.run(request["turn_id"], limit=1, task_ids=[tid])
    elif action["action"] == "complete_agent_task":
        record_demo(e, tid, request["turn_id"])
    elif action["action"] == "prepare_joint_review":
        with e.locked():
            write_review_packet(e)
elif mode == "wrong-turn":
    result["turn_id"] = "other"
output.write_text(json.dumps(result))
with pathlib.Path(request["events_path"]).open("w") as stream:
    for sequence, kind in enumerate(("started", "interrupted" if result["status"] == "interrupted" else "completed"), 1):
        event = {"format":EVENT_FORMAT, **{k:request[k] for k in ("turn_id", "run_id", "session_id")},
                 "sequence":sequence, "type":kind, "data":{}}
        if mode == "wrong-event":
            event["session_id"] = "another-session"
        stream.write(json.dumps(event) + "\\n")
if mode == "empty-events":
    pathlib.Path(request["events_path"]).write_text("")
if mode == "bad-exit":
    raise SystemExit(9)
'''


class AgentRunnerTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ReviewedHarnessTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.worker = self.case.root / "bridge space & worker.py"
        self.worker.write_text(WORKER)
        self.descriptor = self.case.root / "agent.json"
        self.note = self.case.root / "user-request.txt"
        self.note.write_text("Authorized scoped checks and recovery observations")

    def adapter(self, mode="work", **overrides):
        value = {"format": FORMAT, "id": "generic-test", "mode": "managed", "cwd": ".",
            "command": ["{python}", str(self.worker), str(ROOT), "{request}", "{result}", mode],
            "timeout_seconds": 10, "idle_timeout_seconds": 5, "supports_resume": True, **overrides}
        runtime.write_json(self.descriptor, value)
        return self.descriptor

    def start(self, e, scope=None):
        with e.locked():
            record(e.root, "start", {"action": "start", "session_id": "user-1", "request": "Run the authorized checks",
                "scope": scope or {"mode": "tasks", "task_ids": ["contracts", "build"]}, "evidence": [str(self.note)]}, e)

    def invocations(self, e):
        path = e.root / "agent-invocations.txt"
        return path.read_text().splitlines() if path.exists() else []

    def test_claimed_completion_continues_until_scoped_evidence_passes(self):
        e = self.case.ready()
        self.start(e)
        result = drive(e.root, self.adapter(), "drive")
        self.assertTrue(result["can_end_turn"])
        self.assertEqual(result["turns"], 2)
        self.assertFalse(result["quality_gate_granted"])
        self.assertFalse(result["migration_complete"])
        self.assertEqual(len(self.invocations(e)), 2)
        for receipt_path in (e.root / "agent-turns").glob("*/receipt.json"):
            self.assertEqual(read_json(receipt_path)["event_count"], 2)
        self.assertEqual(Harness(e.root).status()["final_review"]["status"], "blocked")
        self.assertTrue(drive(e.root, self.descriptor, "drive")["replayed"])
        self.assertEqual(len(self.invocations(e)), 2)

    def test_lying_agent_hits_no_progress_budget_and_never_grants_completion(self):
        e = self.case.ready()
        self.start(e)
        result = drive(e.root, self.adapter("pretend"), "drive", max_no_progress=2)
        self.assertEqual(result["status"], "no_progress")
        self.assertFalse(result["can_end_turn"])
        self.assertEqual(len(self.invocations(e)), 2)
        self.assertEqual(metrics(e.root)["denied_end_checks"], 3)

    def test_review_scope_stops_with_current_packet_and_without_implementing(self):
        e = self.case.init()
        self.start(e, {"mode": "review", "capability_ids": ["dynamics"]})
        result = drive(e.root, self.adapter(), "review")
        self.assertTrue(result["can_end_turn"])
        self.assertEqual(result["turns"], 2)
        status = Harness(e.root).status()
        self.assertEqual(status["joint_review"][0]["status"], "awaiting_human")
        self.assertEqual({r["id"]: r for r in status["tasks"]}["implement"]["status"], "awaiting_human")

    def test_independent_work_finishes_before_waiting_for_external_hold(self):
        e = self.case.ready()
        self.start(e, {"mode": "full"})
        with e.locked():
            record(e.root, "hold", {"action":"hold", "session_id":"user-1", "id":"offline", "task_ids":["build"],
                "reason":"Signing unavailable", "resume_condition":"Signing returns", "expires_at":time.time()+100,
                "evidence":[str(self.note)]}, e)
        result = drive(e.root, self.adapter(), "drive")
        self.assertEqual(result["status"], "waiting_external")
        self.assertEqual(result["turns"], 1)
        self.assertEqual({r["id"]:r for r in Harness(e.root).status()["tasks"]}["contracts"]["status"], "passed")

    def test_scoped_wait_does_not_dispatch_unrequested_independent_tasks(self):
        e = self.case.ready()
        self.start(e, {"mode":"tasks", "task_ids":["build"]})
        with e.locked():
            record(e.root, "hold", {"action":"hold", "session_id":"user-1", "id":"offline", "task_ids":["build"],
                "reason":"Signing unavailable", "resume_condition":"Signing returns", "expires_at":time.time()+100,
                "evidence":[str(self.note)]}, e)
        result = drive(e.root, self.adapter(), "drive")
        self.assertEqual(result["status"], "waiting_external")
        self.assertEqual(result["turns"], 0)
        self.assertEqual({r["id"]:r for r in Harness(e.root).status()["tasks"]}["contracts"]["status"], "ready")

    def test_initial_human_gate_returns_only_after_packet_is_prepared(self):
        e = self.case.init()
        self.start(e, {"mode": "full"})
        result = drive(e.root, self.adapter(), "review")
        self.assertEqual(result["status"], "waiting_human")
        self.assertEqual(result["turns"], 2)
        with self.assertRaisesRegex(ValueError, "--resume"):
            drive(e.root, self.descriptor, "new-op")

    def test_one_domain_waiting_for_human_does_not_stop_another_domains_discovery(self):
        contract = self.case.contract
        cap = copy.deepcopy(contract["capabilities"][0])
        cap.update(id="other", has_ui=False)
        contract["capabilities"].append(cap)
        scope = copy.deepcopy(contract["review_scopes"][0])
        scope.update(id="other", state_ids=[])
        contract["review_scopes"].append(scope)
        point = copy.deepcopy(contract["acceptance"][0])
        point.update(id="other-gifts", capability_id="other")
        contract["acceptance"].append(point)
        for source, tid, dependencies, acceptance in (("discover", "other-discover", [], []),
                ("implement", "other-implement", ["other-discover"], []),
                ("contracts", "other-contracts", ["other-implement"], ["other-gifts"])):
            task = copy.deepcopy(next(t for t in contract["tasks"] if t["id"] == source))
            task.update(id=tid, capability_ids=["other"], depends_on=dependencies, acceptance_ids=acceptance)
            contract["tasks"].append(task)
        next(t for t in contract["tasks"] if t["id"] == "build")["depends_on"].append("other-implement")
        review = next(t for t in contract["tasks"] if t["id"] == "review")
        review["capability_ids"].append("other")
        review["depends_on"].extend(["other-discover", "other-implement", "other-contracts"])
        e = self.case.init()
        record_demo(e, "discover", "discover")
        with e.locked():
            write_review_packet(e)
        self.start(e, {"mode":"full"})
        result = drive(e.root, self.adapter(), "drive")
        self.assertEqual(result["status"], "waiting_human")
        status = Harness(e.root).status()
        rows = {r["id"]:r for r in status["tasks"]}
        self.assertEqual(rows["other-discover"]["status"], "passed")
        self.assertEqual(rows["implement"]["status"], "awaiting_human")
        self.assertEqual(rows["other-implement"]["status"], "awaiting_human")

    def test_bound_interrupted_envelope_resumes_same_conversation(self):
        e = self.case.ready()
        self.start(e, {"mode":"tasks", "task_ids":["contracts"]})
        result = drive(e.root, self.adapter("reconnect"), "drive")
        self.assertTrue(result["can_end_turn"])
        self.assertEqual(result["turns"], 2)
        self.assertEqual([line.split()[-1] for line in self.invocations(e)], ["execute", "resume"])
        self.assertEqual(metrics(e.root)["interruptions"], 1)

    def test_reconnect_budget_is_bounded(self):
        e = self.case.ready()
        self.start(e)
        result = drive(e.root, self.adapter("always-interrupt"), "drive", max_reconnects=1)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(len(self.invocations(e)), 2)
        self.assertFalse(result["can_end_turn"])

    def test_adapter_without_resume_support_never_automatically_reconnects(self):
        e = self.case.ready()
        self.start(e)
        result = drive(e.root, self.adapter("always-interrupt", supports_resume=False), "drive")
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(len(self.invocations(e)), 1)

    def test_interrupted_business_attempt_prevents_automatic_agent_reconnect(self):
        e = self.case.ready()
        self.start(e)
        process = runtime.run_process
        def interrupted(*args, **kwargs):
            process(*args, **kwargs)
            raise KeyboardInterrupt("Original build result not yet committed")
        with patch("engine.runtime.run_process", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                e.run("interrupted-build", task_ids=["build"])
        result = drive(e.root, self.adapter("always-interrupt"), "drive", resume=True)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(len(self.invocations(e)), 1)
        self.assertEqual(len(list((e.root / "attempts").glob("build-*/process.json"))), 1)

    def test_missing_result_is_not_replayed_and_retry_requires_evidence(self):
        e = self.case.ready()
        self.start(e)
        result = drive(e.root, self.adapter("crash"), "drive")
        self.assertEqual(result["status"], "in_doubt")
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            drive(e.root, self.descriptor, "again", resume=True)
        self.assertEqual(len(self.invocations(e)), 1)
        pending = inspect_control(e.root)["pending_turn"]
        value = {"turn_id":pending["turn_id"], "action":"retry", "reason":"Checked original process and effects"}
        with self.assertRaisesRegex(ValueError, "evidence"):
            reconcile(e.root, "recover", value)
        value["evidence"] = [str(self.note)]
        outcome = reconcile(e.root, "recover", value)
        self.assertFalse(outcome["replay_performed"])
        self.assertTrue(reconcile(e.root, "recover", value)["replayed"])
        self.assertEqual(len(self.invocations(e)), 1)

    def test_cross_turn_result_is_rejected_and_end_check_stays_closed(self):
        e = self.case.ready()
        self.start(e)
        self.assertEqual(drive(e.root, self.adapter("wrong-turn"), "drive")["status"], "in_doubt")
        with e.locked():
            record(e.root, "resume", {"action":"resume", "session_id":"user-1", "summary":"Inspect original execution"}, e)
            result = end_check(e, "end", {"session_id":"user-1", "reason":"request_complete"})
        self.assertFalse(result["allowed"])
        self.assertEqual(result["next_action"]["action"], "inspect_agent_turn")
        before = (e.root / "agent-controller.json").read_bytes()
        self.assertEqual(recovery(e)["state"], "interrupted")
        self.assertEqual(before, (e.root / "agent-controller.json").read_bytes())

    def test_failed_process_cannot_claim_completed(self):
        e = self.case.ready()
        self.start(e)
        result = drive(e.root, self.adapter("bad-exit"), "drive")
        self.assertEqual(result["status"], "in_doubt")
        self.assertFalse(result["can_end_turn"])
        pending = inspect_control(e.root)["pending_turn"]
        with self.assertRaisesRegex(ValueError, "successful"):
            reconcile(e.root, "accept", {"turn_id":pending["turn_id"], "action":"accept", "reason":"Inspect failed process",
                                         "evidence":[str(self.note)]})

    def test_hung_process_times_out_and_is_not_automatically_restarted(self):
        e = self.case.ready()
        self.start(e)
        result = drive(e.root, self.adapter("hang", timeout_seconds=0.15, idle_timeout_seconds=0.1), "drive")
        self.assertEqual(result["status"], "in_doubt")
        observation = inspect_control(e.root)["attempt_observation"]
        self.assertIn(observation["process"]["status"], {"timed_out", "idle_timeout"})
        self.assertEqual(len(self.invocations(e)), 1)

    def test_cross_session_events_are_rejected(self):
        e = self.case.ready()
        self.start(e)
        result = drive(e.root, self.adapter("wrong-event"), "drive")
        self.assertEqual(result["status"], "in_doubt")
        self.assertIn("Agent event belongs", result["error"])

    def test_optional_empty_event_file_does_not_invalidate_a_valid_turn(self):
        e = self.case.ready()
        self.start(e)
        result = drive(e.root, self.adapter("empty-events"), "drive", max_turns=1)
        self.assertEqual(result["status"], "turn_budget")
        self.assertIsNone(inspect_control(e.root)["pending_turn"])

    def test_spawn_failure_is_recoverable_without_fabricating_a_pid(self):
        e = self.case.ready()
        self.start(e)
        with patch("subprocess.Popen", side_effect=OSError("Executable disappeared before launch")):
            result = drive(e.root, self.adapter(), "drive")
        self.assertEqual(result["status"], "in_doubt")
        pending = inspect_control(e.root)["pending_turn"]
        result = reconcile(e.root, "retry", {"turn_id":pending["turn_id"], "action":"retry", "reason":"Observed pre-spawn failure",
                                            "evidence":[str(self.note)]})
        self.assertTrue(result["process_observation"]["process_never_started"])
        self.assertEqual(self.invocations(e), [])

    def test_crash_after_successful_child_accepts_result_without_reexecution(self):
        e = self.case.ready()
        self.start(e, {"mode":"tasks", "task_ids":["contracts"]})
        process = runtime.run_process
        def disconnected(*args, **kwargs):
            process(*args, **kwargs)
            raise KeyboardInterrupt("Host disappeared after child exited")
        with patch("agent_runner.runtime.run_process", side_effect=disconnected):
            self.assertEqual(drive(e.root, self.adapter(), "drive")["status"], "in_doubt")
        pending = inspect_control(e.root)["pending_turn"]
        reconcile(e.root, "accept", {"turn_id":pending["turn_id"], "action":"accept", "reason":"Original child and result inspected",
                                     "evidence":[str(self.note)]})
        result = drive(e.root, self.descriptor, "drive", resume=True)
        self.assertTrue(result["can_end_turn"])
        self.assertEqual(len(self.invocations(e)), 1)

    def test_live_process_prevents_reconciliation(self):
        e = self.case.ready()
        self.start(e)
        drive(e.root, self.adapter("crash"), "drive")
        pending = inspect_control(e.root)["pending_turn"]
        with patch.object(Harness, "confirm_process_stopped", side_effect=ValueError("Process still exists")):
            with self.assertRaisesRegex(ValueError, "still exists"):
                reconcile(e.root, "retry", {"turn_id":pending["turn_id"], "action":"retry", "reason":"Observed original",
                    "evidence":[str(self.note)]})
        self.assertIsNotNone(inspect_control(e.root)["pending_turn"])

    def test_changed_inputs_invalidate_replayed_success(self):
        e = self.case.ready()
        self.start(e)
        self.assertTrue(drive(e.root, self.adapter(), "drive")["can_end_turn"])
        (self.case.root / "harmony/dynamics.py").write_text("changed inputs")
        result = drive(e.root, self.descriptor, "drive")
        self.assertFalse(result["can_end_turn"])
        self.assertEqual(result["status"], "stale_operation")
        self.assertEqual(len(self.invocations(e)), 2)

    def test_enforcement_inspection_does_not_execute_agent_or_claim_host_hook(self):
        result = inspect_adapter(self.adapter())
        self.assertTrue(result["enforcement_available"])
        self.assertFalse(result["active_enforcement"])
        runtime.write_json(self.descriptor, {"format":FORMAT, "id":"external", "mode":"host_hook"})
        result = inspect_adapter(self.descriptor, require_enforcement=True)
        self.assertEqual(result["status"], "enforcement_unavailable")
        self.assertFalse(result["automatic_host_hook"])
        self.assertEqual(inspect_adapter(self.descriptor)["status"], "declared_only")

    def test_stall_budget_does_not_reset_when_operation_is_replayed(self):
        e = self.case.ready()
        self.start(e)
        descriptor = self.adapter("pretend")
        self.assertEqual(drive(e.root, descriptor, "drive", max_turns=1)["status"], "turn_budget")
        self.assertTrue(drive(e.root, descriptor, "drive", max_turns=1)["replayed"])
        self.assertEqual(len(self.invocations(e)), 1)
        with self.assertRaisesRegex(ValueError, "different inputs"):
            drive(e.root, descriptor, "drive", max_turns=2)

    def test_controller_lease_prevents_concurrent_dispatch(self):
        e = self.case.ready()
        self.start(e)
        descriptor = self.adapter()
        with runtime.file_lock(e.root / "agent-controller.lock", timeout=0) as acquired:
            self.assertTrue(acquired)
            with self.assertRaisesRegex(ValueError, "busy"):
                drive(e.root, descriptor, "drive")
        self.assertEqual(self.invocations(e), [])

    def test_corrupt_controller_cannot_allow_end_even_when_scoped_tasks_pass(self):
        e = self.case.ready()
        self.start(e, {"mode":"tasks", "task_ids":["contracts"]})
        e.run("contracts", task_ids=["contracts"])
        (e.root / "agent-controller.json").write_text("truncated")
        result = end_check(e, "end", {"session_id":"user-1", "reason":"request_complete"})
        self.assertFalse(result["allowed"])
        self.assertEqual(recovery(e)["state"], "interrupted")

    def test_structurally_corrupt_controller_remains_diagnosable(self):
        e = self.case.ready()
        self.start(e)
        runtime.write_json(e.root / "agent-controller.json", {"format":"harness-agent-controller/1",
            "run_id":e.state["run_id"], "active":None, "operations":{"broken":[]}})
        result = recovery(e)
        self.assertTrue(result["agent_control"]["journal_error"])
        self.assertEqual(result["next_action"]["action"], "inspect_agent_turn")

    def test_unknown_scope_in_session_is_reported_without_hiding_core_tasks(self):
        e = self.case.ready()
        self.start(e)
        path = e.root / "agent-session.json"
        journal = read_json(path)
        journal["sessions"]["user-1"]["scope"]["task_ids"] = ["missing-task"]
        runtime.write_json(path, journal)
        result = recovery(e)
        self.assertTrue(result["journal_error"])
        self.assertEqual(result["next_action"]["action"], "repair_session_journal")
        self.assertTrue(e.status()["tasks"])

    def test_historical_request_scope_does_not_override_current_contract_scope(self):
        e = self.case.ready()
        self.start(e)
        path = e.root / "agent-session.json"
        journal = read_json(path)
        historical = copy.deepcopy(journal["sessions"]["user-1"])
        historical.update(id="historical", state="ended", scope={"mode":"tasks", "task_ids":["old-task"]})
        journal["sessions"]["historical"] = historical
        runtime.write_json(path, journal)
        result = recovery(e)
        self.assertIsNone(result["journal_error"])
        self.assertEqual(result["session"]["id"], "user-1")

    def test_cli_enforces_veto_exit_status(self):
        e = self.case.ready()
        self.start(e)
        result = subprocess.run([sys.executable, str(ROOT / "migration.py"), "agent-drive", "--run-dir", str(e.root),
            "--adapter", str(self.adapter("pretend")), "--operation-id", "cli", "--max-turns", "1"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "turn_budget")


if __name__ == "__main__":
    unittest.main()
