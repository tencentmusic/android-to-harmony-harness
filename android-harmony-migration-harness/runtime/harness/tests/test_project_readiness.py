import json
import copy
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))
from demo import prepare, record_demo, approval_demo
from engine import Harness, runtime, read_json
from project import readiness, verify, reconcile
from project import cache_dir


class ProjectReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = patch.dict("os.environ", {"MIGRATION_HARNESS_PROFILE_CACHE": str(Path(self.temp.name) / "profile-cache")})
        environment.start()
        self.addCleanup(environment.stop)
        self.root = Path(self.temp.name) / "case"
        self.path = prepare(self.root)
        self.contract = read_json(self.path)

    def test_inventory_is_not_verified_readiness(self):
        e = Harness.init(self.path, self.root / "run")
        context = e.project_context()
        self.assertEqual(context["project_readiness"]["status"], "not_configured")
        self.assertTrue(context["tools"][0]["available"])

    def test_approval_registration_closes_review_wait(self):
        e = Harness.init(self.path, self.root / "run")
        record_demo(e, "discover", "discover")
        e.timer("waiting", "joint-wait", "review_wait", "start")
        with patch("engine.time.time", return_value=e.state["timers"]["joint-wait"]["started_at"] + 10):
            approval_demo(e)
        span = e.metrics()["agent_wall_spans"][0]
        self.assertIn("ended_at", span)
        self.assertEqual(span["elapsed_seconds"], 10)

    def configure(self):
        profile_path = self.root / "tools/project.json"
        profile = read_json(profile_path)
        probe = self.root / "tools/probe.py"
        probe.write_text("""import json, sys
from pathlib import Path
request, result = map(Path, sys.argv[1:])
r = json.loads(request.read_text())
note = result.parent / 'validation.log'
note.write_text('Baseline invocation and assertions passed')
result.write_text(json.dumps({'verified': True, 'input_digest': r['input_digest'], 'evidence': [str(note)]}))
""")
        (self.root / "harmony/build.json").write_text('{"module":"hap"}')
        (self.root / "harmony/test.json").write_text('{"module":"hsp"}')
        for cid, config in (("build", "build.json"), ("hsp-test", "test.json")):
            profile.setdefault("execution_capabilities", []).append({"id": cid, "version": "1",
                "tool_ids": ["python-suite"], "inputs": [{"root": "harmony", "path": config},
                    {"root": "tools", "path": "probe.py"}],
                "probe": {"cwd": "tools", "argv": [sys.executable, str(probe), "{request}", "{result}"]},
                "command": self.contract["tasks"][3]["command"]})
        runtime.write_json(profile_path, profile)
        return profile, profile_path

    def test_reuse_across_runs_and_source_edits_without_probe(self):
        profile, path = self.configure()
        roots = self.contract["roots"]
        self.assertEqual(readiness(profile, roots, path, True)["status"], "needs_verification")
        first = verify(profile, roots, path, ["build", "hsp-test"], True)
        self.assertEqual(first["status"], "verified")
        first_receipts = [r["receipt"] for r in first["capabilities"]]
        (self.root / "harmony/dynamics.py").write_text("def gift_quantities(): return [1, 10, 100]\n")
        for number in range(2):
            e = Harness.init(self.path, self.root / ("run-" + str(number)))
            current = e.project_context()["project_readiness"]
            self.assertEqual(current["status"], "verified")
            self.assertEqual([r["receipt"] for r in current["capabilities"]], first_receipts)
        self.assertEqual(len(list(cache_dir(path).glob("*/*/receipt.json"))), 2)

    def test_targeted_invalidation_and_no_demo_promotion(self):
        profile, path = self.configure()
        roots = self.contract["roots"]
        verify(profile, roots, path, ["build", "hsp-test"], True)
        (self.root / "harmony/test.json").write_text('{"module":"hsp-v2"}')
        rows = {r["id"]: r for r in readiness(profile, roots, path, True)["capabilities"]}
        self.assertEqual(rows["build"]["status"], "verified")
        self.assertEqual(rows["hsp-test"]["status"], "stale")
        self.assertEqual(readiness(profile, roots, path, False)["status"], "needs_verification")
        verify(profile, roots, path, ["hsp-test"], True)
        self.assertEqual(readiness(profile, roots, path, True)["status"], "verified")
        (self.root / "tools/probe.py").write_text("raise RuntimeError('adapter changed')")
        self.assertTrue(all(r["status"] == "stale" for r in readiness(profile, roots, path, True)["capabilities"]))

    def test_missing_or_failed_capability_blocks_only_consumer(self):
        profile, path = self.configure()
        self.contract["tasks"][3]["project_capability_ids"] = ["build"]
        self.contract["tasks"][3]["project_command"] = "build"
        runtime.write_json(self.path, self.contract)
        e = Harness.init(self.path, self.root / "run")
        record_demo(e, "discover", "discover")
        approval_demo(e)
        record_demo(e, "implement", "implement")
        rows = {r["id"]: r for r in e.status()["tasks"]}
        self.assertEqual(rows["build"]["status"], "input_missing")
        self.assertEqual(rows["contracts"]["status"], "ready")
        verify(profile, self.contract["roots"], path, ["build"], True)
        rows = {r["id"]: r for r in e.status()["tasks"]}
        self.assertEqual(rows["build"]["status"], "ready", rows)
        e.run("build-candidate", task_ids=["build"])
        self.assertEqual(e.receipt("build")["kind"], "build")
        self.assertIn("/attempts/", e.receipt("build")["package"]["path"])

    def test_failed_probe_revokes_previous_readiness(self):
        profile, path = self.configure()
        roots = self.contract["roots"]
        verify(profile, roots, path, ["build"], True)
        with patch("project.runtime.run_process", return_value={"status": "exited", "exit_code": 1, "elapsed_seconds": .1}) as runner:
            def fail(argv, **kw):
                runtime.write_json(kw["progress_path"], {"status": "exited", "exit_code": 1})
                Path(kw["log_path"]).write_text("failed validation")
                return {"status": "exited", "exit_code": 1, "elapsed_seconds": .1}
            runner.side_effect = fail
            verify(profile, roots, path, ["build"], True)
        self.assertEqual(readiness(profile, roots, path, True)["capabilities"][0]["status"], "stale")

    def test_cli_onboarding_before_run_and_relative_roots(self):
        profile, path = self.configure()
        roots_path = self.root / "roots.json"
        runtime.write_json(roots_path, self.contract["roots"])
        argv = [sys.executable, str(ROOT / "migration.py"), "project-verify", "--profile", str(path),
                "--roots", str(roots_path), "--capabilities", "hsp-test", "--demo"]
        result = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value["verification_succeeded"])
        self.assertEqual(value["status"], "needs_verification")
        self.assertFalse((self.root / "run").exists())
        self.contract["roots"] = {name: name for name in self.contract["roots"]}
        runtime.write_json(self.path, self.contract)
        self.assertEqual(Harness.init(self.path, self.root / "run").project_context()["project_readiness"]["capabilities"][1]["status"], "verified")

    def test_partial_approval_keeps_other_scope_waiting(self):
        cap = copy.deepcopy(self.contract["capabilities"][0])
        cap.update(id="other", has_ui=False)
        self.contract["capabilities"].append(cap)
        point = copy.deepcopy(self.contract["acceptance"][0])
        point.update(id="other-gifts", capability_id="other")
        self.contract["acceptance"].append(point)
        scope = copy.deepcopy(self.contract["review_scopes"][0])
        scope.update(id="other", state_ids=[])
        self.contract["review_scopes"].append(scope)
        for task in self.contract["tasks"]:
            if task["kind"] in {"discover", "implement", "contract", "review"}:
                task["capability_ids"].append("other")
            if task["kind"] == "contract":
                task["acceptance_ids"].append("other-gifts")
        runtime.write_json(self.path, self.contract)
        e = Harness.init(self.path, self.root / "run")
        record_demo(e, "discover", "discover")
        e.timer("wait-start", "review", "review_wait", "start")
        note = e.root / "decision.log"
        note.write_text("Synthetic partial decisions")
        gates = e.status()["joint_review"]
        for index, gate in enumerate(gates):
            e.approve("approve-" + str(index), {"gate": "joint", "actor_type": "demo", "reviewer": "fixture",
                "summary": "Synthetic decision", "evidence": [str(note)], "reviews": [{
                    "capability_id": gate["capability_id"], "input_digest": gate["input_digest"],
                    "decisions": {d: "approved" for d in ("requirements", "technical", "ui")}}]})
            self.assertEqual("ended_at" in e.state["timers"]["review"], index == len(gates) - 1)

    def test_interrupted_onboarding_requires_reconcile_without_reexecution(self):
        profile, path = self.configure()
        runner = runtime.run_process
        def interrupted(*args, **kwargs):
            runner(*args, **kwargs)
            raise OSError("Simulated loss before capability receipt commit")
        with patch("project.runtime.run_process", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "Simulated loss"):
                verify(profile, self.contract["roots"], path, ["build"], True)
        with self.assertRaisesRegex(ValueError, "project-reconcile"):
            verify(profile, self.contract["roots"], path, ["build"], True)
        note = self.root / "recovery.log"
        note.write_text("Observed stopped probe with complete process and assertion evidence")
        result = reconcile(profile, self.contract["roots"], path, "build", "Recover complete baseline evidence",
                           [str(note)], "accept", True)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(len(list(cache_dir(path).glob("build/*/process.json"))), 1)

    def test_tool_binary_change_invalidates_only_its_consumers(self):
        profile, path = self.configure()
        compiler = self.root / "tools/hsp-compiler"
        compiler.write_text("#!/bin/sh\nexit 0\n")
        compiler.chmod(0o755)
        profile["tools"].append({"id": "hsp-compiler", "purpose": "Fixture toolchain", "cwd": "tools", "argv": [str(compiler)]})
        profile["execution_capabilities"][1]["tool_ids"].append("hsp-compiler")
        runtime.write_json(path, profile)
        verify(profile, self.contract["roots"], path, ["build", "hsp-test"], True)
        compiler.write_text("#!/bin/sh\n# Toolchain revision 2\nexit 0\n")
        rows = {r["id"]: r for r in readiness(profile, self.contract["roots"], path, True)["capabilities"]}
        self.assertEqual(rows["build"]["status"], "verified")
        self.assertEqual(rows["hsp-test"]["status"], "stale")

    @unittest.skipUnless(os.name == 'posix', 'Retry requires POSIX process-group checks')
    def test_real_probe_interruption_can_be_reconciled_then_reverified(self):
        profile, path = self.configure()
        probe = self.root / 'tools/probe.py'
        original = probe.read_bytes()
        probe.write_text('import time; time.sleep(60)\n', encoding='utf-8')
        clock = unittest.mock.Mock(wraps=time)
        clock.sleep.side_effect = KeyboardInterrupt('Actual polling interruption')
        with patch.dict(runtime.run_process.__globals__, time=clock):
            with self.assertRaises(KeyboardInterrupt):
                verify(profile, self.contract['roots'], path, ['build'], True)
        pointer = read_json(cache_dir(path) / 'build/latest.json')
        process = read_json(Path(pointer['path']).parent / 'process.json')
        self.assertEqual(process['status'], 'interrupted')
        self.assertGreater(process['pid'], 1)
        with self.assertRaisesRegex(ValueError, 'project-reconcile'):
            verify(profile, self.contract['roots'], path, ['build'], True)
        note = self.root / 'interruption-diagnosis.log'
        note.write_text('Interrupted probe has no live process or process group', encoding='utf-8')
        result = reconcile(profile, self.contract['roots'], path, 'build', 'Resume after interrupted probe',
                           [str(note)], 'retry', True)
        self.assertEqual(result['status'], 'needs_verification')
        probe.write_bytes(original)
        self.assertTrue(verify(profile, self.contract['roots'], path, ['build'], True)['verification_succeeded'])

    def test_probe_launch_failure_can_be_reconciled_without_a_pid(self):
        profile, path = self.configure()
        with patch.object(runtime.subprocess, 'Popen', side_effect=OSError('Cannot launch probe')):
            with self.assertRaisesRegex(OSError, 'Cannot launch probe'):
                verify(profile, self.contract['roots'], path, ['build'], True)
        pointer = read_json(cache_dir(path) / 'build/latest.json')
        process = read_json(Path(pointer['path']).parent / 'process.json')
        self.assertFalse(process['process_started'])
        self.assertIsNone(process['pid'])
        note = self.root / 'launch-diagnosis.log'
        note.write_text('Probe launch failed before process creation', encoding='utf-8')
        reconcile(profile, self.contract['roots'], path, 'build', 'Restore the probe executable',
                  [str(note)], 'retry', True)
        self.assertTrue(verify(profile, self.contract['roots'], path, ['build'], True)['verification_succeeded'])

    def test_final_approval_closes_only_final_wait(self):
        e = Harness.init(self.path, self.root / "run")
        record_demo(e, "discover", "discover")
        approval_demo(e)
        record_demo(e, "implement", "implement")
        e.run("execute")
        record_demo(e, "review", "review")
        e.timer("final-wait", "final", "review_wait", "start")
        self.assertEqual(e.state["timers"]["final"]["gate"], "final")
        approval_demo(e, "final", "final-decision")
        self.assertEqual(e.state["timers"]["final"]["ended_by"], "approval_registration")


if __name__ == "__main__":
    unittest.main()
