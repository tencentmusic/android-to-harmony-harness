import copy
import itertools
import json
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))
from execution import (DeviceSuite, ExecutionFailure, detector_self_test, layout_matches,
                       network_exit_matches, package_manifest, validate_build_manifest)
from demo import prepare, record_demo, approval_demo
from engine import Harness, runtime, read_json, file_record
from harmony_adapter import run_recipe, test_build_argv as build_test_command


class AdapterGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_transport_assertion_preserves_interceptor_bug(self):
        def production_request(transport, common_interceptor):
            request = {"boundary": "transport", "method": "GET", "url": "https://example.test/soldierInfo?platId=7",
                       "headers": [["appId", "defaultAppId"]]}
            common_interceptor(request)
            return transport(request)
        expectation = {"url": "https://example.test/soldierInfo", "headers": {"appId": "defaultAppId"}, "query": {"platId": "7"}}
        transport = lambda request: network_exit_matches(request, expectation)
        self.assertFalse(production_request(transport, lambda r: r["headers"].append(["appid", "3485"])))
        self.assertTrue(production_request(transport, lambda r: None))
        self.assertFalse(network_exit_matches({"boundary": "transport", "url": "https://example.test/soldierInfo?platId=7",
                                               "headers": [["APPID", "3485"]]}, expectation))
        self.assertFalse(network_exit_matches({"boundary": "transport", "url": "https://example.test/soldierInfo?platId=8", "headers": []},
                                             {"url": "https://example.test/soldierInfo?platId=7"}))
        with self.assertRaisesRegex(ValueError, "transport boundary"):
            network_exit_matches({"boundary": "repository"}, expectation)

    def suite(self, mutation=None, stale=False):
        counter, clicks = itertools.count(), []
        context = {"room_id": "room-a", "battle_id": "battle-a", "orientation": "portrait",
                   "feature_enabled": True, "package_sha256": "current-candidate"}
        def observe():
            number = next(counter)
            observed = copy.deepcopy(context)
            if mutation and number > 0:
                observed.update(mutation)
            return {"capture_id": "capture-" + str(number), "captured_at": time.time() - (30 if stale else 0),
                    "context": observed, "controls": [{"id": "card", "visible": True, "enabled": True,
                                                        "effect": "navigation", "hit_testable": True, "bounds": [number, 0, 10, 10]}]}
        suite = DeviceSuite("current-candidate", observe, lambda control, snap: clicks.append((control, snap)),
                            required_context={k: v for k, v in context.items() if k != "package_sha256"})
        return suite, clicks

    def test_two_actions_retarget_from_fresh_layouts(self):
        suite, clicks = self.suite()
        suite.action("open", "card")
        suite.action("return", "card")
        self.assertEqual([c[0]["bounds"][0] for c in clicks], [0, 2])
        self.assertEqual(len(suite.steps), 2)
        self.assertEqual(len(suite.capture_ids), 4)

    def test_stale_layout_or_spending_target_never_clicked(self):
        suite, clicks = self.suite(stale=True)
        with self.assertRaises(ExecutionFailure) as error:
            suite.action("open", "card")
        self.assertEqual(error.exception.failure["category"], "tool_failure")
        self.assertEqual(clicks, [])
        suite, clicks = self.suite()
        original = suite.observe
        def unsafe():
            value = original()
            value["controls"][0]["effect"] = "spend"
            return value
        suite.observe = unsafe
        with self.assertRaises(ExecutionFailure):
            suite.action("open", "card")
        self.assertEqual(clicks, [])

    def test_mid_action_battle_or_orientation_change_is_environment_failure(self):
        for changed in ({"battle_id": "expired"}, {"orientation": "landscape"}):
            with self.subTest(changed=changed):
                suite, clicks = self.suite(mutation=changed)
                with self.assertRaises(ExecutionFailure) as error:
                    suite.action("open-card", "card")
                self.assertEqual(error.exception.failure["category"], "environment_changed")
                self.assertEqual(error.exception.failure["recovery_point"], "open-card")
                self.assertEqual(len(clicks), 1)
                self.assertEqual(suite.steps, [])

    def test_disabled_feature_or_wrong_package_blocks_first_action(self):
        for changed in ({"feature_enabled": False}, {"package_sha256": "old"}):
            suite, clicks = self.suite()
            original = suite.observe
            def changed_snapshot():
                value = original()
                value["context"].update(changed)
                return value
            suite.observe = changed_snapshot
            with self.assertRaises(ExecutionFailure) as error:
                suite.action("enter", "card")
            self.assertEqual(error.exception.failure["category"], "precondition_missing")
            self.assertEqual(clicks, [])

    def test_detector_distinguishes_card_activity_and_wrong_identity(self):
        rule = {"required_controls": ["profile-card", "user-id"], "context": {"target_user_id": "42"}}
        good = {"controls": [{"id": cid, "visible": True} for cid in rule["required_controls"]], "context": {"target_user_id": "42"}}
        samples = [{"role": "confirmed-profile", "input": good, "expected": True},
                   {"role": "activity-popup", "input": {"controls": [{"id": "campaign", "visible": True}]}, "expected": False},
                   {"role": "wrong-identity", "input": {**good, "context": {"target_user_id": "43"}}, "expected": False}]
        self.assertEqual(len(detector_self_test(lambda v: layout_matches(v, rule), samples)), 3)
        with self.assertRaises(ExecutionFailure) as error:
            detector_self_test(lambda v: False, samples)
        self.assertEqual(error.exception.failure["category"], "tool_failure")

    def test_package_uses_exact_manifest_and_signature_bound_to_hash(self):
        module = self.root / "entry-signed.hap"
        with zipfile.ZipFile(module, "w") as archive:
            archive.writestr("module.json", '{"name":"entry"}')
        signature = self.root / "signature.json"
        runtime.write_json(signature, {"verified": True, "package_sha256": file_record(module)["sha256"],
                                       "verifier": "offline-test-only"})
        manifest = package_manifest(self.root / "candidate.app", [{"archive_name": "entry.hap", "path": str(module),
                                                                   "signature_evidence": str(signature)}])
        raw = {"package_path": str(self.root / "candidate.app"), "output_manifest": manifest}
        evidence = [file_record(module), file_record(signature), manifest["package"]]
        self.assertEqual(validate_build_manifest(raw, evidence), manifest)
        runtime.write_json(signature, {"verified": True, "package_sha256": "old", "verifier": "offline-test-only"})
        with self.assertRaisesRegex(ValueError, "Signature verification"):
            package_manifest(self.root / "wrong.app", [{"archive_name": "entry.hap", "path": str(module),
                                                        "signature_evidence": str(signature)}])

    def test_hsp_test_lifecycle_uses_gen_on_device_not_assemble_hsp(self):
        self.assertEqual(build_test_command("hvigorw", "shared", "hsp")[1], "genOnDeviceTestHap")
        self.assertEqual(build_test_command("hvigorw", "entry", "hap")[1], "assembleHap")
        with self.assertRaisesRegex(ValueError, "module type"):
            build_test_command("hvigorw", "unknown", "guess")
        tool = self.root / "hvigor-test"
        tool.write_text("#!/usr/bin/env python3\nimport sys\nassert sys.argv[1] == 'genOnDeviceTestHap'\nprint('HSP test build channel checked')\n")
        tool.chmod(0o755)
        request = self.root / "request.json"
        output = self.root / "result.json"
        recipe = self.root / "recipe.json"
        runtime.write_json(request, {"format": "migration-project-capabilities/1", "input_digest": "fixture",
                                     "roots": {"harmony": str(self.root)}})
        runtime.write_json(recipe, {"test_module": {"root": "harmony", "name": "shared", "type": "hsp", "hvigor": str(tool)},
            "steps": [{"id": "check-results", "phase": "assert", "command": {"cwd": "harmony", "argv": [sys.executable, "-c",
                "import json,sys; from pathlib import Path; p=Path(sys.argv[1]); (p/'normalized.json').write_text(json.dumps({'verified':True}))", "{output}"]}}],
            "normalized_result": "normalized.json"})
        result = run_recipe(request, output, recipe)
        self.assertTrue(result["verified"])
        self.assertEqual([s["phase"] for s in result["lifecycle_steps"]], ["build", "assert"])
        self.assertIn("build", result["phase_seconds"])

    def test_hvigor_prefix_preserves_windows_paths_and_runs_script_entry(self):
        windows = [r'C:\Program Files\DevEco\node.exe', r'C:\Program Files\DevEco\hvigorw.js']
        argv = build_test_command(windows, 'shared', 'hsp')
        self.assertEqual(argv[:3], [*windows, 'genOnDeviceTestHap'])
        for invalid in (None, [], ['node', ''], {'node': 'hvigor'}):
            with self.assertRaisesRegex(ValueError, 'argv prefix'):
                build_test_command(invalid, 'shared', 'hsp')
        tool = self.root / 'hvigor entry.py'
        tool.write_text("import sys\nassert sys.argv[1] == 'genOnDeviceTestHap'\n", encoding='utf-8')
        request, output, recipe = [self.root / name for name in ('request.json', 'result.json', 'recipe.json')]
        runtime.write_json(request, {'format': 'migration-project-capabilities/1', 'input_digest': 'fixture',
                                     'roots': {'harmony': str(self.root)}})
        runtime.write_json(recipe, {'test_module': {'root': 'harmony', 'name': 'shared', 'type': 'hsp',
            'build_argv_prefix': [sys.executable, str(tool)]}, 'steps': [{
                'id': 'results', 'phase': 'assert', 'command': {'cwd': 'harmony', 'argv': [sys.executable, '-c',
                    "import json,sys; from pathlib import Path; Path(sys.argv[1]).joinpath('normalized.json').write_text(json.dumps({'verified':True}))",
                    '{output}']}}], 'normalized_result': 'normalized.json'})
        result = run_recipe(request, output, recipe)
        self.assertTrue(result['verified'])
        self.assertEqual(result['lifecycle_steps'][0]['process']['argv'][:2], [sys.executable, str(tool)])

    def test_recipe_failure_runs_cleanup_and_keeps_classified_receipt(self):
        request, output, recipe = [self.root / name for name in ("request.json", "result.json", "recipe.json")]
        runtime.write_json(request, {"format": "migration-project-capabilities/1", "input_digest": "fixture",
                                     "roots": {"harmony": str(self.root)}})
        runtime.write_json(recipe, {"steps": [
            {"id": "test", "phase": "assert", "command": {"cwd": "harmony", "argv": [sys.executable, "-c", "raise SystemExit(1)"]}},
            {"id": "cleanup", "phase": "cleanup", "command": {"cwd": "harmony", "argv": [sys.executable, "-c", "print('cleanup complete')"]}}],
            "normalized_result": "should-not-be-read.json"})
        result = run_recipe(request, output, recipe)
        self.assertFalse(result["verified"])
        self.assertEqual(result["failure"]["category"], "tool_failure")
        self.assertIn("cleanup", result["phase_seconds"])

    def test_missing_lifecycle_program_still_runs_cleanup(self):
        request, output, recipe = [self.root / name for name in ("request.json", "result.json", "recipe.json")]
        runtime.write_json(request, {"format": "migration-project-capabilities/1", "input_digest": "fixture",
                                     "roots": {"harmony": str(self.root)}})
        runtime.write_json(recipe, {"steps": [
            {"id": "install", "phase": "install", "command": {"cwd": "harmony", "argv": [str(self.root / "missing-program")]}},
            {"id": "cleanup", "phase": "cleanup", "command": {"cwd": "harmony", "argv": [sys.executable, "-c", "print('restored')"]}}],
            "normalized_result": "should-not-be-read.json"})
        result = run_recipe(request, output, recipe)
        self.assertFalse(result["verified"])
        self.assertEqual(result["failure"]["code"], "lifecycle_launch")
        self.assertEqual(result["lifecycle_steps"][-1]["id"], "cleanup")


class ExecutionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "case"
        self.path = prepare(self.root)
        self.contract = read_json(self.path)

    def ready(self):
        runtime.write_json(self.path, self.contract)
        e = Harness.init(self.path, self.root / "run")
        record_demo(e, "discover", "discover")
        approval_demo(e)
        record_demo(e, "implement", "implement")
        return e

    def test_real_result_validation_overrides_fake_pass_on_final_request(self):
        task = next(t for t in self.contract["tasks"] if t["id"] == "contracts")
        task["execution_policy"] = {"network_exit": {"gifts": {"url": "https://example.test/soldierInfo",
                                                               "headers": {"appId": "defaultAppId"}}}}
        e = self.ready()
        capture = e.root / "request-capture.json"
        runtime.write_json(capture, {"boundary": "transport", "url": "https://example.test/soldierInfo",
                                     "headers": [["appid", "3485"]]})
        raw = {"points": [{"acceptance_id": "gifts", "status": "checked", "evidence": [str(capture)],
                           "network_capture": str(capture), "observations": [{"assertion_id": "gift-values", "actual": [1, 10, 100]}]}]}
        _, result = e.validate_result(e.tasks["contracts"], raw)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["failures"][0]["code"], "verification_policy")
        runtime.write_json(capture, {"boundary": "transport", "url": "https://example.test/soldierInfo",
                                     "headers": [["appId", "defaultAppId"]]})
        self.assertTrue(e.validate_result(e.tasks["contracts"], raw)[1]["accepted"])

    def test_problem_history_survives_new_operation_and_input_version(self):
        (self.root / "tools/settings.json").write_text('{"omit_popup":true}')
        e = self.ready()
        e.run("first")
        note = e.root / "diagnosis.log"
        note.write_text("Inspect missing photo source assertion")
        e.retry("retry-1", "visual", "Inspect omission", [str(note)])
        e.run("second")
        problems = e.metrics()["problems"]
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["occurrences"], 2)
        self.assertEqual(problems[0]["category"], "evidence_missing")
        with self.assertRaisesRegex(ValueError, "new diagnostic evidence"):
            e.retry("retry-2", "visual", "Inspect omission", [str(note)])
        self.assertNotIn("retry-2", Harness(e.root).state["operations"])
        note.write_text("New evidence: omitted popup branch not emitted by adapter")
        e.retry("retry-2", "visual", "Inspect omission", [str(note)])
        e.run("third")
        self.assertEqual(e.metrics()["problems"][0]["occurrences"], 3)
        self.assertGreater(e.metrics()["executor_cost_seconds"]["rework"], 0)
        source = self.root / "harmony/dynamics.py"
        source.write_text(source.read_text() + "\n# Business revision 2\n")
        record_demo(e, "implement", "new-implementation-inputs")
        problem_id = e.metrics()["problems"][0]["id"]
        e.run("new-inputs", problem_ids=[problem_id])
        row = next(r for r in e.status()["tasks"] if r["id"] == "visual")
        self.assertEqual(row["status"], "needs_diagnosis")
        self.assertEqual(row["attempts"], 3)
        rebuild = e.receipt("build")
        self.assertEqual(rebuild["problem_ids"], [problem_id])

    def test_wait_can_overlap_independent_work_and_union_is_not_sum(self):
        e = self.ready()
        e.timer("work-start", "work", "implementation", "start")
        e.timer("wait-start", "wait", "review_wait", "start")
        metrics = e.metrics()
        self.assertEqual(len(metrics["agent_wall_spans"]), 2)
        self.assertLessEqual(metrics["agent_wall_union_seconds"], sum(s["elapsed_seconds"] for s in metrics["agent_wall_spans"]) + .001)
        self.assertIsNone(metrics["unrecorded_external_seconds"])

    def test_detector_failure_blocks_only_its_consumer(self):
        tools = self.root / "tools"
        rule = {"required_controls": ["profile-card"]}
        runtime.write_json(tools / "detector.json", rule)
        runtime.write_json(tools / "good.json", {"controls": [{"id": "activity", "visible": True}]})
        runtime.write_json(tools / "bad.json", {"controls": []})
        def ref(name):
            return {"root": "tools", "path": name, "locator": "Confirmed detector fixture"}
        self.contract["tasks"][4]["execution_policy"] = {"detectors": {"editor-device": {"rule": ref("detector.json"),
            "samples": [{"role": "confirmed-profile", "input": ref("good.json"), "expected": True},
                        {"role": "similar-popup", "input": ref("bad.json"), "expected": False}]}}}
        e = self.ready()
        e.run("cheap-checks", task_ids=["contracts", "build"])
        rows = {r["id"]: r for r in e.status()["tasks"]}
        self.assertEqual(rows["contracts"]["status"], "passed")
        self.assertEqual(rows["build"]["status"], "passed")
        self.assertEqual(rows["device"]["status"], "input_missing")
        self.assertEqual(rows["device"]["failure"]["category"], "tool_failure")

    def test_grouped_guarded_suite_exports_point_receipts_to_real_validator(self):
        task = self.contract["tasks"][4]
        task["execution_policy"] = {"device_guard": {"room_id": "room-a", "battle_id": "battle-a", "orientation": "portrait"}}
        e = self.ready()
        e.run("build", task_ids=["build"])
        package = e.package(e.tasks["device"])["sha256"]
        counter = itertools.count()
        log = e.root / "device.log"
        log.write_text("Synthetic fresh layout and action driver")
        screenshot = self.root / "android/baseline.png"
        context = {"device_id": "fixture", "system_version": "fixture", "app_id": "fixture", "account_ref": "fixture",
                   "package_sha256": package, "synthetic": True, "normal_entry": False, "room_id": "room-a",
                   "battle_id": "battle-a", "orientation": "portrait"}
        def observe():
            return {"capture_id": str(next(counter)), "captured_at": time.time(), "context": context, "controls": [],
                    "evidence": [str(screenshot), str(log)]}
        suite = DeviceSuite(package, observe, lambda *args: None, required_context=task["execution_policy"]["device_guard"])
        points = [{"acceptance_id": aid, "observe": lambda snapshot: [{"assertion_id": "observed", "actual": True}]}
                  for aid in task["acceptance_ids"]]
        raw = suite.run(points, e.root / "suite")
        self.assertTrue(e.validate_result(e.tasks["device"], raw)[1]["accepted"])
        proof = Path(raw["points"][0]["suite_receipt"])
        journal = read_json(proof)
        journal["captures"][0]["context"]["battle_id"] = "expired"
        runtime.write_json(proof, journal)
        with self.assertRaisesRegex(ValueError, "Suite environment changed"):
            e.validate_result(e.tasks["device"], raw)

    def test_wrong_semantic_baseline_is_a_tool_failure(self):
        tools = self.root / "tools"
        def ref(name):
            return {"root": "tools", "path": name, "locator": "Confirmed source fixture"}
        runtime.write_json(tools / "rule.json", {"required_controls": ["profile-card"]})
        runtime.write_json(tools / "positive.json", {"controls": [{"id": "profile-card", "visible": True}]})
        runtime.write_json(tools / "negative.json", {"controls": []})
        runtime.write_json(tools / "baseline-layout.json", {"image_sha256": file_record(self.root / "android/baseline.png")["sha256"],
            "controls": [{"id": "activity-popup", "visible": True}]})
        self.contract["tasks"][4]["execution_policy"] = {"detectors": {"editor-device": {"rule": ref("rule.json"),
            "samples": [{"role": "profile", "expected": True, "input": ref("positive.json")},
                        {"role": "other", "expected": False, "input": ref("negative.json")}],
            "baseline": {"image": self.contract["states"][0]["baselines"][0], "layout": ref("baseline-layout.json")}}}}
        e = self.ready()
        e.run("build", task_ids=["build"])
        row = next(r for r in e.status()["tasks"] if r["id"] == "device")
        self.assertEqual(row["status"], "input_missing")
        self.assertEqual(row["failure"]["code"], "wrong_baseline_state")

    def test_replayed_old_layout_receipt_cannot_pass_current_executor(self):
        self.contract["tasks"][4]["execution_policy"] = {"device_guard": {"room_id": "a"}}
        adapter = self.root / "tools/adapter.py"
        source = adapter.read_text()
        injected = """if kind == 'device':
        result['context']['room_id'] = 'a'
        for point in result['points']:
            proof = folder / (point['acceptance_id'] + '-suite.json')
            proof.write_text(json.dumps({'package_sha256': request['package']['sha256'], 'captures': [
                {'capture_id': 'old-layout', 'captured_at': 1, 'used_at': 1.01,
                 'context': {'room_id': 'a', 'package_sha256': request['package']['sha256']}}]}))
            point['suite_receipt'] = str(proof)
            point['evidence'].append(str(proof))
    """
        adapter.write_text(source.replace("result_path.write_text(json.dumps(result)", injected + "result_path.write_text(json.dumps(result)"))
        e = self.ready()
        e.run("replayed-evidence")
        row = next(r for r in e.status()["tasks"] if r["id"] == "device")
        self.assertEqual(row["status"], "needs_diagnosis")
        self.assertEqual(row["problems"][0]["category"], "evidence_missing")
        adapter.write_text(adapter.read_text().replace("'captured_at': 1, 'used_at': 1.01", "'captured_at': time.time(), 'used_at': time.time()"))
        approval_demo(e, operation_id="fresh-materials")
        record_demo(e, "implement", "fresh-driver")
        e.run("current-evidence")
        self.assertEqual(next(r for r in e.status()["tasks"] if r["id"] == "device")["status"], "passed")

    def test_replayed_transport_capture_cannot_pass_current_executor(self):
        self.contract["tasks"][2]["execution_policy"] = {"network_exit": {"gifts": {"url": "https://example.test/exit"}}}
        adapter = self.root / "tools/adapter.py"
        source = adapter.read_text()
        injected = """if kind == 'contract':
        capture = folder / 'exit.json'
        capture.write_text(json.dumps({'boundary': 'transport', 'url': 'https://example.test/exit',
                                      'headers': [], 'input_digest': 'old-inputs', 'captured_at': time.time()}))
        result['points'][0]['network_capture'] = str(capture)
        result['points'][0]['evidence'].append(str(capture))
    """
        adapter.write_text(source.replace("result_path.write_text(json.dumps(result)", injected + "result_path.write_text(json.dumps(result)"))
        e = self.ready()
        e.run("replayed-evidence")
        row = next(r for r in e.status()["tasks"] if r["id"] == "contracts")
        self.assertEqual(row["status"], "needs_diagnosis")
        adapter.write_text(adapter.read_text().replace("'input_digest': 'old-inputs'", "'input_digest': request['input_digest']"))
        approval_demo(e, operation_id="fresh-materials")
        record_demo(e, "implement", "fresh-driver")
        e.run("current-evidence")
        self.assertEqual(next(r for r in e.status()["tasks"] if r["id"] == "contracts")["status"], "passed")


if __name__ == "__main__":
    unittest.main()
