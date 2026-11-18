import copy
import itertools
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import runtime, read_json, file_record
from execution import DeviceSuite, ExecutionFailure, validate_execution_window
from suite import validate, validate_point


class DeclarativeSuiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.request = self.root / "request.json"
        runtime.write_json(self.request, {"input_digest": "current", "attempt": "one"})
        self.context = {"device_id": "device-a", "package_sha256": "candidate"}
        self.screen, self.actions, self.counter = "home", [], itertools.count()
        self.template = {"format": "migration-device-suite/1", "points": [{"acceptance_id": "details", "steps": [
            {"id": "open", "op": "action", "selector": "details", "postcondition": {"required_controls": ["title"]}},
            {"id": "ready", "op": "checkpoint", "condition": {"required_controls": ["title"]}}],
            "observations": [{"assertion_id": "title-visible", "condition": {"required_controls": ["title"]}}]}]}

    def suite(self, crash=False):
        def observe():
            number = next(self.counter)
            path = self.root / ("capture-" + str(number) + ".json")
            snapshot = {"capture_id": "capture-" + str(number), "captured_at": time.time(),
                "context": copy.deepcopy(self.context), "controls": [{"id": "details" if self.screen == "home" else "title",
                    "visible": True, "enabled": True, "effect": "navigation", "hit_testable": True}]}
            runtime.write_json(path, snapshot)
            return {**snapshot, "evidence": [str(path)]}
        def act(control, snapshot):
            self.actions.append(control["id"])
            self.screen = "details"
            if crash:
                raise KeyboardInterrupt("Host lost after click")
        return DeviceSuite("candidate", observe, act, required_context={"device_id": "device-a"})

    def run_suite(self, **kw):
        return self.suite().run_declarative(self.template, self.request, self.root / "suite", **kw)

    def test_declared_steps_observations_and_compact_summary(self):
        result = self.run_suite()
        self.assertEqual(self.actions, ["details"])
        self.assertEqual(result["points"][0]["observations"], [{"assertion_id": "title-visible", "actual": True}])
        self.assertEqual(result["summary"]["checked"], 1)
        self.assertEqual(len(list((self.root / "suite").glob("*-step.json"))), 2)
        with self.assertRaisesRegex(ValueError, "explicit resume"):
            self.run_suite()
        result = self.run_suite(resume=True)
        self.assertEqual(self.actions, ["details"])
        self.assertEqual(result["summary"]["checked"], 1)

    def test_crash_after_click_never_replays_pending_action(self):
        with self.assertRaises(KeyboardInterrupt):
            self.suite(crash=True).run_declarative(self.template, self.request, self.root / "suite")
        with self.assertRaisesRegex(ValueError, "never replay"):
            self.run_suite(resume=True)
        self.assertEqual(self.actions, ["details"])
        note = self.root / "diagnosis.log"
        note.write_text("Observed details after interrupted action")
        result = self.run_suite(resume=True, reconcile={"step_id": "open", "evidence": [str(note)]})
        self.assertEqual(self.actions, ["details"])
        self.assertEqual(result["summary"]["checked"], 1)
        self.assertTrue(read_json(self.root / "suite/open-step.json")["recovered"])

    def test_wrong_postcondition_requires_restoration_and_preserves_pending(self):
        with self.assertRaises(KeyboardInterrupt):
            self.suite(crash=True).run_declarative(self.template, self.request, self.root / "suite")
        self.screen = "home"
        note = self.root / "diagnosis.log"
        note.write_text("Actual screen is home")
        with self.assertRaisesRegex(ValueError, "postcondition"):
            self.run_suite(resume=True, reconcile={"step_id": "open", "evidence": [str(note)]})
        self.assertEqual(read_json(self.root / "suite/suite-state.json")["pending"], "open")
        self.assertEqual(self.actions, ["details"])

    def test_pre_action_failure_resumes_after_target_is_restored(self):
        suite = self.suite()
        observe = suite.observe
        def disabled_target():
            snapshot = observe()
            snapshot["controls"][0]["enabled"] = False
            return snapshot
        suite.observe = disabled_target
        result = suite.run_declarative(self.template, self.request, self.root / "suite")
        self.assertEqual(result["points"][0]["failure"]["code"], "unsafe_target")
        self.assertFalse(suite.action_started)
        self.assertEqual(self.actions, [])
        self.assertIsNone(read_json(self.root / "suite/suite-state.json")["pending"])
        self.assertEqual(self.run_suite(resume=True)["summary"]["checked"], 1)
        self.assertEqual(self.actions, ["details"])

    def test_later_pre_action_failure_does_not_inherit_previous_action_started(self):
        self.template["points"][0]["steps"].append({"id": "next", "op": "action", "selector": "title",
            "postcondition": {"required_controls": ["title"]}})
        suite = self.suite()
        observe = suite.observe
        def disabled_second_target():
            snapshot = observe()
            if self.screen == "details":
                snapshot["controls"][0]["enabled"] = False
            return snapshot
        suite.observe = disabled_second_target
        result = suite.run_declarative(self.template, self.request, self.root / "suite")
        self.assertEqual(result["points"][0]["failure"]["step"], "next")
        self.assertEqual(self.actions, ["details"])
        self.assertIsNone(read_json(self.root / "suite/suite-state.json")["pending"])
        self.assertEqual(self.run_suite(resume=True)["summary"]["checked"], 1)
        self.assertEqual(self.actions, ["details", "title"])

    def test_failure_after_action_invocation_keeps_pending_even_with_precondition_category(self):
        suite = self.suite()
        act = suite.act
        def failed_after_action(control, snapshot):
            act(control, snapshot)
            raise ExecutionFailure("precondition_missing", "connection_lost", "open", "Inspect action effects", "open")
        suite.act = failed_after_action
        suite.run_declarative(self.template, self.request, self.root / "suite")
        self.assertEqual(self.actions, ["details"])
        self.assertEqual(read_json(self.root / "suite/suite-state.json")["pending"], "open")
        with self.assertRaisesRegex(ValueError, "never replay"):
            self.run_suite(resume=True)
        self.assertEqual(self.actions, ["details"])

    def test_package_request_and_environment_changes_cannot_reuse_passes(self):
        self.run_suite()
        self.context["device_id"] = "device-b"
        with self.assertRaises(ExecutionFailure):
            self.run_suite(resume=True)
        self.context["device_id"] = "device-a"
        runtime.write_json(self.request, {"input_digest": "changed", "attempt": "two"})
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_suite(resume=True)

    def test_changed_capture_and_template_are_rejected(self):
        self.run_suite()
        (self.root / "capture-0.json").write_text("Changed evidence")
        with self.assertRaisesRegex(ValueError, "Changed evidence"):
            self.run_suite(resume=True)
        invalid = copy.deepcopy(self.template)
        invalid["points"][0]["steps"][0]["op"] = "eval"
        with self.assertRaisesRegex(ValueError, "Unknown suite step"):
            validate(invalid)

    def test_assert_failure_keeps_precise_step_and_skips_descendants(self):
        self.template["points"][0]["steps"][1].update(op="assert", condition={"required_controls": ["missing"]})
        self.template["points"].append({"acceptance_id": "later", "observations": [{"assertion_id": "later", "path": ["context"]}]})
        result = self.run_suite()
        self.assertEqual(result["points"][0]["failure"]["step"], "ready")
        self.assertEqual(result["points"][0]["failure"]["category"], "product_failure")
        self.assertEqual(result["points"][1]["status"], "not_checked")

    def test_declared_suite_binding_validates_current_request_and_capture_window(self):
        result = self.run_suite()
        path = self.root / "template.json"
        runtime.write_json(path, self.template)
        task = {"device_suite": {"root": "tools", "path": path.name, "locator": "Device steps"}}
        point = result["points"][0]
        validate_point(task, result, point, [file_record(p) for p in point["evidence"]], {"tools": str(self.root)})
        request = read_json(self.request)
        validate_execution_window(task, result, request, {"started_at": 0, "finished_at": time.time() + 1})
        with self.assertRaisesRegex(ValueError, "current executor time window"):
            validate_execution_window(task, result, request, {"started_at": time.time() + 1, "finished_at": time.time() + 2})
        with self.assertRaisesRegex(ValueError, "another request"):
            validate_execution_window(task, result, {"input_digest": "other"}, {"started_at": 0, "finished_at": time.time() + 1})

    def test_point_evidence_is_local_and_request_selection_is_explicit(self):
        self.template["points"].append({"acceptance_id": "later", "observations": [
            {"assertion_id": "current-page", "condition": {"required_controls": ["title"]}}]})
        result = self.run_suite()
        self.assertNotIn(str(self.root / "capture-0.json"), result["points"][1]["evidence"])
        selected_dir = self.root / "selected"
        runtime.write_json(self.request, {"input_digest": "current", "requested_acceptance_ids": ["later"]})
        selected = self.suite().run_declarative(self.template, self.request, selected_dir)
        self.assertEqual([p["acceptance_id"] for p in selected["points"]], ["later"])
        self.assertEqual(self.actions, ["details"])


if __name__ == "__main__":
    unittest.main()
