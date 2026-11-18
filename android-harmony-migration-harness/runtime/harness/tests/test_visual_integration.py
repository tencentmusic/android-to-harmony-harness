"""Exercise built-in pixel verification through the reviewed Harness lifecycle."""
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))
from contract import validate_contract
from demo import approval_demo, prepare, record_demo
from engine import Harness, read_json, runtime
from foundation import file_record
from visual import compare_images
from test_plan import compile_contract


class VisualHarnessIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="harness-visual-integration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "case"
        self.contract_path = prepare(self.root)
        self.contract = read_json(self.contract_path)
        image = Image.new("RGB", (64, 64), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((4, 4, 58, 19), fill="#12763f")
        draw.rectangle((8, 25, 53, 56), fill="#b6293d")
        draw.line((0, 63, 63, 0), fill="black", width=2)
        image.save(self.root / "android/baseline.png")
        visual = self.visual_task(self.contract)
        visual.pop("command")
        visual["visual_comparison"] = {
            aid: {"assertion_id": "observed", "capture_from": {
                "task_id": "device", "acceptance_id": aid.replace("-visual", "-device")}}
            for aid in visual["acceptance_ids"]
        }

    @staticmethod
    def visual_task(contract):
        return next(task for task in contract["tasks"] if task["id"] == "visual")

    @staticmethod
    def regions():
        return {"viewport": {"width": 64, "height": 64}, "masks": [],
                "required_rois": [{"id": "top-half", "x": 0, "y": 0, "width": 64, "height": 32}]}

    def ready(self, different_capture=False):
        if different_capture:
            Image.new("RGB", (64, 64), "black").save(self.root / "tools/different.png")
            adapter = self.root / "tools/adapter.py"
            text = adapter.read_text()
            original = '(Path(request["roots"]["android"]) / "baseline.png").read_bytes()'
            replacement = '(Path(request["roots"]["tools"]) / "different.png").read_bytes()'
            self.assertIn(original, text)
            adapter.write_text(text.replace(original, replacement))
        runtime.write_json(self.contract_path, self.contract)
        experiment = Harness.init(self.contract_path, self.root / "run")
        record_demo(experiment, "discover", "discover")
        approval_demo(experiment)
        record_demo(experiment, "implement", "implement")
        return experiment

    def execute(self, different_capture=False):
        experiment = self.ready(different_capture)
        result = experiment.run("pixel-checks")
        self.assertEqual(result["executed"], ["contracts", "build", "device", "visual"])
        return experiment

    def visual_result(self, experiment):
        # Linked device evidence also contains result.json. Select the visual attempt itself.
        attempt = Path(experiment.state["tasks"]["visual"]["receipt"]["path"]).parent
        return read_json(attempt / "result.json")

    def alternate_report(self, experiment, raw, *, diagnostic=False, baseline=None, capture=None, regions=None, state_id=None):
        raw = copy.deepcopy(raw)
        point = raw["points"][0]
        original = read_json(point["visual_comparison"]["path"])
        output = experiment.root / "alternative-report"
        report = compare_images(
            baseline or original["inputs"]["baseline"]["path"],
            capture or original["inputs"]["capture"]["path"],
            output, state_id=state_id or original["state_id"], regions=regions, diagnostic=diagnostic)
        point["visual_comparison"] = file_record(output / "comparison.json")
        paths = [record["path"] for record in [*report["inputs"].values(), *report["artifacts"].values()]]
        point["evidence"] = list(dict.fromkeys([*point["evidence"], *paths, str(output / "comparison.json")]))
        point["observations"][0]["actual"] = report["status"] == "passed"
        return raw

    def test_builtin_adapter_runs_real_pixels_and_demo_never_grants_delivery(self):
        experiment = self.execute()
        self.assertEqual(Path(experiment.tasks["visual"]["command"]["argv"][1]), ROOT / "visual_adapter.py")
        result = self.visual_result(experiment)
        self.assertTrue(experiment.receipt("visual")["accepted"])
        for point in result["points"]:
            report = read_json(point["visual_comparison"]["path"])
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["full"]["score"], 1.)
            self.assertEqual(report["full"]["normalized_mae"], 0.)
            self.assertEqual(report["inputs"]["baseline"]["width"], 64)
            self.assertIn("visual.py", {Path(record["path"]).name for record in experiment.receipt("visual")["inputs"]["engine_files"]})
        record_demo(experiment, "review", "review")
        approval_demo(experiment, "final", "final")
        self.assertEqual(experiment.verify()["status"], "demo_only")

    def test_true_observation_without_a_pixel_report_is_rejected(self):
        experiment = self.execute()
        raw = self.visual_result(experiment)
        raw["points"][0].pop("visual_comparison")
        with self.assertRaisesRegex(ValueError, "comparison report evidence"):
            experiment.validate_result(experiment.tasks["visual"], raw)

    def test_true_observation_cannot_override_a_failed_pixel_comparison(self):
        experiment = self.execute(different_capture=True)
        raw = self.visual_result(experiment)
        self.assertFalse(experiment.receipt("visual")["accepted"])
        self.assertFalse(raw["points"][0]["observations"][0]["actual"])
        raw["points"][0]["observations"][0]["actual"] = True
        with self.assertRaisesRegex(ValueError, "observation differs from recomputed"):
            experiment.validate_result(experiment.tasks["visual"], raw)

    def test_diagnostic_report_cannot_satisfy_native_acceptance(self):
        experiment = self.execute()
        raw = self.alternate_report(experiment, self.visual_result(experiment), diagnostic=True)
        with self.assertRaisesRegex(ValueError, "Diagnostic image comparison"):
            experiment.validate_result(experiment.tasks["visual"], raw)

    def test_equal_pixels_from_another_baseline_are_rejected(self):
        experiment = self.execute()
        other = experiment.root / "other-baseline.png"
        other.write_bytes((self.root / "android/baseline.png").read_bytes())
        raw = self.alternate_report(experiment, self.visual_result(experiment), baseline=other)
        with self.assertRaisesRegex(ValueError, "different declared baseline"):
            experiment.validate_result(experiment.tasks["visual"], raw)

    def test_equal_pixels_from_another_capture_are_rejected(self):
        experiment = self.execute()
        other = experiment.root / "other-capture.png"
        other.write_bytes((self.root / "android/baseline.png").read_bytes())
        raw = self.alternate_report(experiment, self.visual_result(experiment), capture=other)
        with self.assertRaisesRegex(ValueError, "current device dependency capture"):
            experiment.validate_result(experiment.tasks["visual"], raw)

    def test_report_for_another_state_is_rejected(self):
        experiment = self.execute()
        raw = self.alternate_report(experiment, self.visual_result(experiment), state_id="wrong-state")
        with self.assertRaisesRegex(ValueError, "state differs from acceptance"):
            experiment.validate_result(experiment.tasks["visual"], raw)

    def test_rehashed_report_with_changed_score_is_recomputed_and_rejected(self):
        experiment = self.execute()
        raw = self.visual_result(experiment)
        point = raw["points"][0]
        path = Path(point["visual_comparison"]["path"])
        report = read_json(path)
        report["full"]["score"] = 0.9
        report["report_digest"] = runtime.digest({k: v for k, v in report.items() if k != "report_digest"})
        runtime.write_json(path, report)
        point["visual_comparison"] = file_record(path)
        with self.assertRaisesRegex(ValueError, "differs from recomputed pixels"):
            experiment.validate_result(experiment.tasks["visual"], raw)

    def test_genuine_report_with_unreviewed_regions_is_rejected(self):
        experiment = self.execute()
        raw = self.alternate_report(experiment, self.visual_result(experiment), regions=self.regions())
        with self.assertRaisesRegex(ValueError, "regions differ from reviewed policy"):
            experiment.validate_result(experiment.tasks["visual"], raw)

    def test_contract_rejects_capture_from_a_different_state(self):
        spec = self.visual_task(self.contract)["visual_comparison"]["editor-visual"]
        spec["capture_from"]["acceptance_id"] = "photo-source-device"
        with self.assertRaisesRegex(ValueError, "same declared state"):
            validate_contract(self.contract, self.root)

    def test_contract_rejects_capture_from_a_nondevice_task(self):
        visual = self.visual_task(self.contract)
        visual["depends_on"].append("contracts")
        visual["visual_comparison"]["editor-visual"]["capture_from"] = {
            "task_id": "contracts", "acceptance_id": "gifts"}
        with self.assertRaisesRegex(ValueError, "direct device dependency"):
            validate_contract(self.contract, self.root)

    def test_contract_rejects_null_visual_comparison(self):
        self.visual_task(self.contract)["visual_comparison"] = None
        with self.assertRaisesRegex(ValueError, "Configure every owned visual comparison point"):
            validate_contract(self.contract, self.root)

    def test_real_visual_contract_requires_comparison_binding(self):
        self.contract["demo"] = False
        visual = self.visual_task(self.contract)
        visual.pop("visual_comparison")
        visual["command"] = next(t["command"] for t in self.contract["tasks"] if t["id"] == "device")
        with self.assertRaisesRegex(ValueError, "Real visual tasks require visual_comparison"):
            validate_contract(self.contract, self.root)

    def test_plan_preserves_visual_binding_through_execution(self):
        visual = self.visual_task(self.contract)
        bindings = visual.pop("visual_comparison")
        plan = {"version": 1, "changes": [{"id": "layout", "capability_id": "dynamics",
            "before": "Old layout", "after": "Updated layout",
            "sources": [{"root": "tools", "path": "project.md", "locator": "Layout change"}]}],
            "scenarios": [], "excluded_states": []}
        for sid in ("editor", "photo-source"):
            plan["scenarios"].append({"id": sid, "capability_id": "dynamics", "change_ids": ["layout"],
                "state_ids": [sid], "behavior": "Render " + sid, "risk": "Missing layout",
                "grouping_reason": "One screenshot state", "observable_ui": True,
                "verifications": [{"kind": kind, "task_id": kind, "reason": "Check " + kind,
                    "assertions": [{"id": "observed", "operator": "truthy", "expected": True}],
                    **({"visual_comparison": bindings[sid + "-visual"]} if kind == "visual" else {})}
                    for kind in ("device", "visual")]})
        self.contract = compile_contract(self.contract, plan)
        self.assertEqual(self.visual_task(self.contract).get("visual_comparison"), bindings)
        experiment = self.ready()
        experiment.run("planned-pixels")
        self.assertTrue(experiment.receipt("visual")["accepted"])
        changed = copy.deepcopy(self.contract)
        self.visual_task(changed)["visual_comparison"]["editor-visual"]["regions"] = self.regions()
        with self.assertRaisesRegex(ValueError, "Visual bindings differ from test_plan"):
            validate_contract(changed, self.root)

    def test_unbound_real_result_is_rejected_even_for_an_already_loaded_run(self):
        experiment = self.execute()
        raw = self.visual_result(experiment)
        experiment.contract["demo"] = False
        task = copy.deepcopy(experiment.tasks["visual"])
        task.pop("visual_comparison")
        with self.assertRaisesRegex(ValueError, "Real visual tasks require visual_comparison"):
            experiment.validate_result(task, raw)

    def test_regions_revision_invalidates_the_joint_approval(self):
        experiment = self.ready()
        self.assertEqual(experiment.joint_status("dynamics")["status"], "approved")
        revised = copy.deepcopy(experiment.contract)
        self.visual_task(revised)["visual_comparison"]["editor-visual"]["regions"] = self.regions()
        runtime.write_json(self.contract_path, revised)
        experiment.revise("regions-revision", self.contract_path, "Review a changed pixel comparison region")
        experiment = Harness(experiment.root)
        self.assertEqual(experiment.joint_status("dynamics")["status"], "stale")
        self.assertEqual(experiment.verify()["status"], "incomplete")

    def test_historical_run_is_rejected_with_actionable_error_and_preserved(self):
        old = self.root / "historical"
        old.mkdir()
        path = old / "experiment-state.json"
        original = b'{"format":"autonomous-migration-experiment/1","preserve":true}\n'
        path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "Unsupported historical run.*fresh directory"):
            Harness(old)
        result = subprocess.run([sys.executable, str(ROOT / "migration.py"), "status", "--run-dir", str(old)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("Unsupported historical run", result.stdout + result.stderr)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual({p.name for p in old.iterdir()}, {"experiment-state.json"})


if __name__ == "__main__":
    unittest.main()
