import copy
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_engine as fixtures
from test_engine import Harness, ROOT, read_json, record_demo, runtime
from engine import STATE


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ReviewedHarnessTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.root

    def note(self, e):
        path = e.root / "diagnosis.txt"
        path.write_text("Synthetic recovery diagnosis; no real device or business effects.\n")
        return [str(path)]

    def adapter_result(self, transform):
        real_run = runtime.run_process

        def execute(argv, **kwargs):
            process = real_run(argv, **kwargs)
            request, result = read_json(argv[-2]), read_json(argv[-1])
            transform(request, result)
            runtime.write_json(Path(argv[-1]), result)
            return process

        return patch("engine.runtime.run_process", side_effect=execute)

    def omit_popup(self, request, result):
        if request["task"]["kind"] == "visual":
            result["points"] = [p for p in result["points"] if p["acceptance_id"] != "photo-source-visual"]

    def partial(self):
        e = self.case.ready()
        with self.adapter_result(self.omit_popup):
            e.run("partial")
        return e

    def interrupt_completed_device(self):
        e = self.case.ready()
        e.run("build", task_ids=["build"])
        real_run = runtime.run_process

        def interrupt(argv, **kwargs):
            real_run(argv, **kwargs)
            raise KeyboardInterrupt()

        with patch("engine.runtime.run_process", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                e.run("interrupted", task_ids=["device"])
        return Harness(e.root)

    def test_asset_change_revokes_approval_and_keeps_review_snapshot(self):
        asset = self.root / "tools/prototype.svg"
        original = '<svg xmlns="http://www.w3.org/2000/svg"><text>Approved</text></svg>'
        asset.write_text(original)
        (self.root / "tools/ui.md").write_text("![Prototype](prototype.svg)\n")
        e = self.case.complete()
        approval = read_json(e.state["joint_reviews"]["dynamics"]["path"])
        snapshot = read_json(approval["snapshot"]["path"])
        archived = next(r["archived"]["path"] for r in snapshot["files"] if r["source"] == str(asset.resolve()))
        asset.write_text(original.replace("Approved", "Changed"))
        self.assertEqual(e.status()["joint_review"][0]["status"], "stale")
        self.assertEqual(e.verify()["status"], "incomplete")
        self.assertEqual(Path(archived).read_text(), original)

    def test_asset_addition_and_deletion_change_review_digest(self):
        e = self.case.ready()
        digest = e.joint_status("dynamics")["input_digest"]
        asset = self.root / "tools/new.css"
        asset.write_text("body { color: red; }\n")
        self.assertNotEqual(e.joint_status("dynamics")["input_digest"], digest)
        asset.unlink()
        self.assertEqual(e.joint_status("dynamics")["input_digest"], digest)

    def test_missing_material_can_be_inspected_and_repaired(self):
        e = self.case.ready()
        (self.root / "tools/ui.md").rename(self.root / "tools/renamed.md")
        e = Harness(e.root)
        self.assertEqual(e.status()["joint_review"][0]["status"], "stale")
        self.assertIn("reason", e.review_packet()["joint_review"][0])
        self.assertEqual(e.inspect("implement")["task"]["status"], "awaiting_human")
        cli = subprocess.run([sys.executable, str(ROOT / "migration.py"), "status", "--run-dir", str(e.root)],
                             capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        revised = copy.deepcopy(self.case.contract)
        revised["review_scopes"][0]["ui"]["path"] = "renamed.md"
        path = self.root / "revised.json"
        runtime.write_json(path, revised)
        status = e.revise("repair", path, "Review document renamed")
        self.assertEqual(status["joint_review"][0]["status"], "stale")
        self.assertIn("input_digest", status["joint_review"][0])

    def test_missing_profile_does_not_disable_diagnostics_or_revision(self):
        e = self.case.ready()
        (self.root / "tools/project.json").rename(self.root / "tools/profile.json")
        e = Harness(e.root)
        self.assertEqual(e.status()["tasks"][0]["status"], "input_missing")
        e.inspect("discover")
        revised = copy.deepcopy(self.case.contract)
        revised["project_profile"]["path"] = "profile.json"
        path = self.root / "revised.json"
        runtime.write_json(path, revised)
        e.revise("repair", path, "Profile renamed")
        self.assertEqual(e.project_context()["project"], "offline-example")

    def test_reconcile_accepts_completed_result_without_running_adapter_again(self):
        e = self.interrupt_completed_device()
        evidence = self.note(e)
        with self.assertRaisesRegex(ValueError, "use reconcile"):
            e.retry("unsafe-retry", "device", "Result already exists", evidence)
        with patch("engine.runtime.run_process", side_effect=AssertionError("Must not rerun")):
            result = e.reconcile("accept", "device", "Confirmed completed adapter", evidence)
            replay = e.reconcile("accept", "device", "Confirmed completed adapter", evidence)
        row = next(r for r in result["tasks"] if r["id"] == "device")
        self.assertEqual((row["status"], row["attempts"]), ("passed", 1))
        self.assertTrue(replay["replayed"])
        self.assertGreater(e.metrics()["executor_by_kind"]["device"]["seconds"], 0)

    def test_reconcile_rejects_drift_and_retains_in_doubt(self):
        e = self.interrupt_completed_device()
        source = self.root / "harmony/dynamics.py"
        source.write_text(source.read_text() + "\n# changed\n")
        with self.assertRaisesRegex(ValueError, "Dependencies|Inputs changed"):
            e.reconcile("bad", "device", "Attempt to reuse old package", self.note(e))
        row = next(r for r in Harness(e.root).status()["tasks"] if r["id"] == "device")
        self.assertEqual(row["status"], "in_doubt")

    def test_receipt_io_failure_preserves_attempt_and_does_not_rerun_adapter(self):
        self.check_receipt_io_failure("receipt.json")

    def test_state_io_failure_rolls_back_partial_receipt_and_metrics(self):
        self.check_receipt_io_failure("state")

    def check_receipt_io_failure(self, failed_write):
        next(t for t in self.case.contract["tasks"] if t["id"] == "device")["independent_points"] = True
        e = self.case.ready()
        e.run("build", task_ids=["build"])
        write = runtime.write_json

        def fail_receipt(path, value):
            if (failed_write == "receipt.json" and Path(path).name == "receipt.json" or
                    failed_write == "state" and Path(path) == e.root / STATE and
                    value["events"][-1]["kind"] == "task_passed"):
                raise OSError("Injected receipt persistence failure")
            write(path, value)

        with patch("engine.runtime.run_process", wraps=runtime.run_process) as adapter:
            with patch("engine.runtime.write_json", side_effect=fail_receipt):
                status = e.run("write-failure", task_ids=["device"])
            self.assertEqual(next(r for r in status["tasks"] if r["id"] == "device")["status"], "in_doubt")
            e = Harness(e.root)
            saved = e.state["tasks"]["device"]
            output = Path(saved["running"]["directory"])
            self.assertTrue((output / "result.json").exists())
            self.assertFalse(saved.get("point_receipts"))
            self.assertFalse(saved.get("phase_seconds"))
            self.assertFalse(saved.get("failed_attempts"))
            with self.assertRaisesRegex(ValueError, "use reconcile"):
                e.retry("retry", "device", "Must reconcile first", self.note(e))
            self.assertEqual(e.run("blocked", task_ids=["device"])["executed"], [])
            e.reconcile("accept", "device", "Recover completed result", self.note(e))
            self.assertEqual(adapter.call_count, 1)
        saved = e.state["tasks"]["device"]
        self.assertEqual(saved["attempts"], 1)
        self.assertEqual(set(saved["point_receipts"]), set(e.tasks["device"]["acceptance_ids"]))
        self.assertEqual(saved["phase_seconds"], read_json(output / "result.json")["phase_seconds"])
        self.assertEqual(self.case.rows(e)["device"]["status"], "passed")

    def test_reconcile_can_be_repeated_after_receipt_io_failure(self):
        e = self.interrupt_completed_device()
        with patch.object(e, "commit_receipt", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(OSError):
                e.reconcile("failed-accept", "device", "Recover result", self.note(e))
        e = Harness(e.root)
        self.assertTrue(e.state["tasks"]["device"]["running"])
        e.reconcile("accept", "device", "Storage repaired", self.note(e))
        output = Path(e.state["tasks"]["device"]["receipt"]["path"]).parent
        self.assertEqual(e.state["tasks"]["device"]["phase_seconds"], read_json(output / "result.json")["phase_seconds"])

    def interrupt_terminal_write(self, error=KeyboardInterrupt):
        e = self.case.ready()
        e.run("build", task_ids=["build"])
        write = runtime.write_json

        def interrupt(path, value):
            if Path(path).name == "process.json" and value.get("status") == "exited":
                raise error("Injected crash before terminal record")
            write(path, value)

        with patch("engine.runtime.write_json", side_effect=interrupt):
            if error is KeyboardInterrupt:
                with self.assertRaises(KeyboardInterrupt):
                    e.run("interrupted", task_ids=["device"])
            else:
                e.run("interrupted", task_ids=["device"])
        return Harness(e.root)

    @unittest.skipUnless(os.name == "posix", "Recovery probes POSIX process groups")
    def test_missing_terminal_record_can_be_explicitly_retried_after_process_exit(self):
        e = self.interrupt_terminal_write()
        output = Path(e.state["tasks"]["device"]["running"]["directory"])
        self.assertEqual(read_json(output / "process.json")["status"], "running")
        self.assertTrue((output / "result.json").exists())
        with self.assertRaisesRegex(ValueError, "use reconcile"):
            e.retry("unsafe", "device", "Missing terminal record", self.note(e))
        with self.assertRaisesRegex(ValueError, "no terminal record"):
            e.reconcile("accept", "device", "Cannot prove success", self.note(e))
        result = subprocess.run([sys.executable, str(ROOT / "migration.py"), "reconcile", "--run-dir", str(e.root),
            "--operation-id", "recover", "--task", "device", "--action", "retry", "--reason",
            "Confirmed synthetic business effects can be repeated", "--evidence", *self.note(e)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        e = Harness(e.root)
        event = next(x for x in e.state["events"] if x["kind"] == "attempt_reconciled")
        self.assertTrue(event["stop_verification"]["process_group_absent"])
        self.assertEqual(read_json(output / "process.json")["status"], "running")
        with patch("engine.runtime.run_process", wraps=runtime.run_process) as adapter:
            e.run("resume", task_ids=["device"])
            self.assertEqual(adapter.call_count, 1)
        self.assertEqual(self.case.rows(e)["device"]["status"], "passed")
        self.assertEqual(e.state["tasks"]["device"]["attempts"], 2)

    @unittest.skipUnless(os.name == "posix", "Recovery probes POSIX process groups")
    def test_terminal_record_io_failure_retains_recoverable_attempt(self):
        e = self.interrupt_terminal_write(OSError)
        self.assertEqual(self.case.rows(e)["device"]["status"], "in_doubt")
        e.reconcile("retry", "device", "Confirmed fixture can be repeated", self.note(e), action="retry")
        self.assertIsNone(e.state["tasks"]["device"]["running"])

    def test_retry_without_result_cannot_bypass_process_reconciliation(self):
        e = self.interrupt_terminal_write()
        output = Path(e.state["tasks"]["device"]["running"]["directory"])
        (output / "result.json").unlink()
        with self.assertRaisesRegex(ValueError, "use reconcile"):
            e.retry("unsafe", "device", "No result yet", self.note(e))
        self.assertEqual(self.case.rows(Harness(e.root))["device"]["status"], "in_doubt")

    @unittest.skipUnless(os.name == "posix", "Recovery probes POSIX process groups")
    def test_missing_terminal_record_blocks_live_or_unverifiable_processes(self):
        e = self.interrupt_terminal_write()
        for pid_error, group_error in ((None, None), (ProcessLookupError(), None),
                                       (PermissionError(), None), (ProcessLookupError(), PermissionError())):
            with self.subTest(pid_error=pid_error, group_error=group_error):
                with patch("engine.os.kill", side_effect=pid_error), patch("engine.os.killpg", side_effect=group_error):
                    with self.assertRaisesRegex(ValueError, "still exists|Cannot verify"):
                        e.reconcile("retry", "device", "Inspect process", self.note(e), action="retry")
                self.assertEqual(self.case.rows(Harness(e.root))["device"]["status"], "in_doubt")

    def test_reconcile_rejects_modified_request(self):
        e = self.interrupt_completed_device()
        request = Path(e.state["tasks"]["device"]["running"]["request"]["path"])
        request.write_text(request.read_text() + "\n")
        for action in ("accept", "retry"):
            with self.assertRaisesRegex(ValueError, "Changed evidence"):
                e.reconcile("bad", "device", "Check request binding", self.note(e), action=action)
        self.assertTrue(Harness(e.root).state["tasks"]["device"]["running"])

    def test_reconcile_rejects_nonterminal_process(self):
        e = self.interrupt_completed_device()
        path = Path(e.state["tasks"]["device"]["running"]["directory"]) / "process.json"
        process = read_json(path)
        process["status"] = "running"
        runtime.write_json(path, process)
        with self.assertRaisesRegex(ValueError, "no terminal record"):
            e.reconcile("bad", "device", "Process still running", self.note(e))

    def test_cli_reconcile_and_point_selection(self):
        e = self.interrupt_completed_device()
        result = subprocess.run([sys.executable, str(ROOT / "migration.py"), "reconcile", "--run-dir", str(e.root),
            "--operation-id", "recover-cli", "--task", "device", "--reason", "Confirmed completed adapter",
            "--evidence", *self.note(e)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, str(ROOT / "migration.py"), "run", "--run-dir", str(e.root),
            "--operation-id", "point-cli", "--points", "photo-source-visual"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        e = Harness(e.root)
        self.assertEqual(e.state["tasks"]["device"]["attempts"], 1)
        points = {p["acceptance_id"]: p["status"] for p in e.status()["points"]}
        self.assertEqual(points["editor-visual"], "not_checked")
        self.assertEqual(points["photo-source-visual"], "passed")

    def test_reconcile_explicit_retry_preserves_attempt_and_idempotency_key(self):
        e = self.interrupt_completed_device()
        old_dir = Path(e.state["tasks"]["device"]["running"]["directory"])
        old_key = read_json(old_dir / "request.json")["idempotency_key"]
        e.reconcile("permit-new-attempt", "device", "Verified fixture can be repeated", self.note(e), action="retry")
        e.run("new-attempt", task_ids=["device"])
        latest_dir = Path(e.state["tasks"]["device"]["receipt"]["path"]).parent
        self.assertNotEqual(old_dir, latest_dir)
        self.assertTrue((old_dir / "result.json").is_file())
        self.assertEqual(read_json(latest_dir / "request.json")["idempotency_key"], old_key)
        self.assertEqual(e.state["tasks"]["device"]["attempts"], 2)

    def test_reconcile_rejects_foreign_process_record(self):
        e = self.interrupt_completed_device()
        path = Path(e.state["tasks"]["device"]["running"]["directory"]) / "process.json"
        process = read_json(path)
        process["argv"][-1] = "another-result.json"
        runtime.write_json(path, process)
        for action in ("accept", "retry"):
            with self.assertRaisesRegex(ValueError, "another attempt"):
                e.reconcile("bad", "device", "Mismatched process record", self.note(e), action=action)

    def test_six_successful_candidates_do_not_exhaust_failure_budget(self):
        e = self.case.ready()
        for index in range(6):
            if index:
                source = self.root / "harmony/dynamics.py"
                source.write_text(source.read_text() + "\n# revision\n")
                record_demo(e, "implement", "implementation-" + str(index))
            status = e.run("candidate-" + str(index))
            self.assertEqual(next(r for r in status["tasks"] if r["id"] == "build")["status"], "passed")
        self.assertEqual(e.state["tasks"]["build"]["attempts"], 6)

    def test_failure_budget_grants_are_audited_and_retry_does_not_reset_failures(self):
        next(t for t in self.case.contract["tasks"] if t["id"] == "visual")["max_attempts"] = 1
        e = self.partial()
        self.assertEqual(self.case.rows(e)["visual"]["status"], "budget_exhausted")
        note = self.note(e)
        e.retry("retry", "visual", "Diagnosed omitted point", note)
        self.assertEqual(e.run("no-budget", task_ids=["visual"])["executed"], [])
        e.extend_budget("grant", "visual", 1, "One targeted retry", note)
        self.assertTrue(e.extend_budget("grant", "visual", 1, "One targeted retry", note)["replayed"])
        status = e.run("resume", point_ids=["photo-source-visual"])
        self.assertEqual(next(r for r in status["tasks"] if r["id"] == "visual")["status"], "passed")
        self.assertEqual(len(e.state["tasks"]["visual"]["failed_attempts"]), 1)
        self.assertTrue(any(event["kind"] == "budget_extended" for event in e.state["events"]))

    def test_point_resume_keeps_valid_point_and_stable_idempotency_keys(self):
        e = self.partial()
        old = e.receipt("visual")
        first_request = read_json(next(r["path"] for r in old["evidence"] if r["path"].endswith("/request.json")))
        original_point = e.state["tasks"]["visual"]["point_receipts"]["editor-visual"]
        e.retry("retry", "visual", "Recover omitted point", self.note(e))
        status = e.run("resume", point_ids=["photo-source-visual"])
        self.assertEqual(status["coverage"]["passed"], 5)
        self.assertEqual(e.state["tasks"]["visual"]["point_receipts"]["editor-visual"], original_point)
        latest_request = read_json(Path(e.state["tasks"]["visual"]["receipt"]["path"]).parent / "request.json")
        self.assertEqual(latest_request["requested_acceptance_ids"], ["photo-source-visual"])
        self.assertEqual(first_request["idempotency_key"], latest_request["idempotency_key"])
        self.assertEqual(first_request["point_idempotency_keys"], latest_request["point_idempotency_keys"])

    def test_environment_change_prevents_point_merge(self):
        e = self.partial()
        e.retry("retry", "visual", "Environment changed", self.note(e))

        def change_environment(request, result):
            if request["task"]["kind"] == "visual":
                result["context"]["environment_digest"] = "new-fixture"

        with self.adapter_result(change_environment):
            status = e.run("resume", point_ids=["photo-source-visual"])
        points = {p["acceptance_id"]: p["status"] for p in status["points"]}
        self.assertEqual(points["editor-visual"], "not_checked")
        self.assertEqual(points["photo-source-visual"], "passed")

    def test_corrupt_point_evidence_only_reopens_that_independent_point(self):
        e = self.case.ready()

        def separate_visual_evidence(request, result):
            if request["task"]["kind"] == "visual":
                for point in result["points"]:
                    source = next(Path(p) for p in point["evidence"] if p.endswith(".png"))
                    target = Path(point["evidence"][0]).parent / (point["acceptance_id"] + ".png")
                    target.write_bytes(source.read_bytes())
                    point["evidence"] = [str(target) if p == str(source) else p for p in point["evidence"]]

        with self.adapter_result(separate_visual_evidence):
            e.run("complete-suite")
        editor = next(p for p in e.status()["points"] if p["acceptance_id"] == "editor-visual")
        shot = next(Path(r["path"]) for r in editor["evidence"] if r["path"].endswith(".png"))
        shot.write_bytes(b"corrupt")
        points = {p["acceptance_id"]: p["status"] for p in e.status()["points"]}
        self.assertEqual(points["editor-visual"], "not_checked")
        self.assertEqual(points["photo-source-visual"], "passed")
        e.run("repair-evidence", task_ids=["visual"])
        request = read_json(Path(e.state["tasks"]["visual"]["receipt"]["path"]).parent / "request.json")
        self.assertEqual(request["requested_acceptance_ids"], ["editor-visual"])
        self.assertEqual(e.status()["coverage"]["passed"], 5)

    def test_new_failure_overrides_previously_passed_point(self):
        e = self.partial()
        prior = copy.deepcopy(next(p for p in e.receipt("visual")["point_results"] if p["acceptance_id"] == "editor-visual"))
        e.retry("retry", "visual", "Recheck both points", self.note(e))

        def fail_editor(request, result):
            if request["task"]["kind"] == "visual":
                result["points"].append({"acceptance_id": "editor-visual", "status": "checked",
                    "evidence": [r["path"] for r in prior["evidence"]],
                    "observations": [{"assertion_id": "observed", "actual": False}]})

        with self.adapter_result(fail_editor):
            status = e.run("resume", point_ids=["photo-source-visual"])
        self.assertEqual(next(p for p in status["points"] if p["acceptance_id"] == "editor-visual")["status"], "failed")

    def test_changed_package_requests_all_points_again(self):
        e = self.partial()
        source = self.root / "harmony/dynamics.py"
        source.write_text(source.read_text() + "\n# changed candidate\n")
        record_demo(e, "implement", "changed-implementation")
        seen = []

        def inspect_request(request, result):
            if request["task"]["kind"] == "visual":
                seen.extend(request["requested_acceptance_ids"])

        with self.adapter_result(inspect_request):
            e.run("new-candidate", task_ids=["visual"])
        self.assertEqual(set(seen), {"editor-visual", "photo-source-visual"})

    def test_coupled_suites_cannot_be_selected_by_point(self):
        e = self.case.ready()
        with self.assertRaisesRegex(ValueError, "independent_points"):
            e.run("unsafe", point_ids=["editor-device"])

    def test_independent_native_points_require_environment_digest(self):
        e = self.case.ready()

        def missing_environment(request, result):
            if request["task"]["kind"] == "visual":
                result["context"].pop("environment_digest")

        with self.adapter_result(missing_environment):
            status = e.run("missing-environment")
        row = next(r for r in status["tasks"] if r["id"] == "visual")
        self.assertEqual(row["status"], "needs_diagnosis")
        self.assertIn("environment_digest", row["reason"])


if __name__ == "__main__":
    unittest.main()
