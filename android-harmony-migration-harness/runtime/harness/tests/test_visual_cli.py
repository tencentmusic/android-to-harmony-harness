import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import migration
from foundation import file_record
from visual_cli import compare_capture


class VisualCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.baseline, self.capture = self.root / "baseline.png", self.root / "capture.png"
        image = Image.new("RGB", (72, 96), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 12, 63, 30), fill="black")
        draw.rectangle((8, 42, 32, 62), fill="green")
        draw.rectangle((40, 42, 63, 62), fill="red")
        draw.rectangle((8, 76, 63, 87), fill="blue")
        image.save(self.baseline)
        image.save(self.capture)
        self.output = self.root / "comparison"

    def cli(self, *arguments):
        return subprocess.run([sys.executable, str(ROOT / "migration.py"), *map(str, arguments)],
                              cwd=self.root, capture_output=True, text=True, timeout=30)

    def compare_cli(self, *arguments):
        return self.cli("compare-visual", "--baseline", self.baseline, "--harmony-capture", self.capture,
                        "--state-id", "profile-card", "--output-dir", self.output, *arguments)

    def assert_image_only(self, result):
        self.assertEqual(result["acceptance_scope"], "image_comparison_only")
        self.assertIs(result["quality_gate_granted"], False)
        self.assertNotIn("delivery", result)

    def test_identical_png_cli_runs_without_migration_run_and_exits_zero(self):
        completed = self.compare_cli()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "passed")
        self.assert_image_only(result)
        comparison = result["comparison"]
        self.assertEqual(comparison["full"]["score"], 1)
        self.assertEqual(comparison["full"]["normalized_mae"], 0)
        self.assertEqual(result["comparison_report"], file_record(self.output / "comparison.json"))
        self.assertEqual(comparison["inputs"]["baseline"]["sha256"], file_record(self.baseline)["sha256"])
        self.assertEqual(comparison["inputs"]["capture"]["sha256"], file_record(self.capture)["sha256"])
        self.assertTrue((self.output / "difference.png").is_file())
        self.assertTrue((self.output / "comparison.png").is_file())
        self.assertFalse((self.root / "harness-state.json").exists())
        self.assertNotIn("continuation", result)

    def test_structural_image_change_exits_one_and_retains_failed_report(self):
        Image.new("RGB", (72, 96), "black").save(self.capture)
        completed = self.compare_cli()
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["comparison"]["full"]["status"], "failed")
        self.assert_image_only(result)
        self.assertEqual(result["comparison_report"], file_record(self.output / "comparison.json"))

    def test_dimension_mismatch_exits_two_without_implicit_resize(self):
        Image.new("RGB", (36, 48), "white").save(self.capture)
        original = file_record(self.capture)
        completed = self.compare_cli()
        self.assertEqual(completed.returncode, 2)
        error = json.loads(completed.stderr)
        self.assertEqual(error["status"], "error")
        self.assertIn("dimensions differ", error["error"])
        self.assertEqual(completed.stdout, "")
        self.assertFalse(self.output.exists())
        self.assertEqual(file_record(self.capture), original)

    def test_resize_without_diagnostic_is_rejected_before_output_creation(self):
        Image.new("RGB", (36, 48), "white").save(self.capture)
        completed = self.compare_cli("--resize-to-baseline")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("requires --diagnostic", json.loads(completed.stderr)["error"])
        self.assertFalse(self.output.exists())
        with self.assertRaisesRegex(ValueError, "requires --diagnostic"):
            compare_capture(self.baseline, self.capture, self.output, state_id="profile-card", resize_to_baseline=True)
        self.assertFalse(self.output.exists())

    def test_explicit_diagnostic_resize_records_original_and_new_hashes_and_dimensions(self):
        with Image.open(self.baseline) as reference:
            reference.resize((36, 48)).save(self.capture)
        original = file_record(self.capture)
        completed = self.compare_cli("--diagnostic", "--resize-to-baseline")
        self.assertIn(completed.returncode, (0, 1), completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assert_image_only(result)
        self.assertTrue(result["diagnostic"])
        self.assertTrue(result["comparison"]["diagnostic"])
        self.assertFalse(result["comparison"]["gate_eligible"])
        transform = result["transform"]
        self.assertEqual(transform["operation"], "explicit_resize_to_baseline")
        self.assertEqual(transform["from_size"], [36, 48])
        self.assertEqual(transform["to_size"], [72, 96])
        self.assertEqual(transform["source"]["sha256"], original["sha256"])
        self.assertEqual((transform["source"]["width"], transform["source"]["height"]), (36, 48))
        self.assertFalse(transform["gate_eligible"])
        normalized = self.output / "normalized-capture.png"
        self.assertEqual(transform["output"], file_record(normalized))
        self.assertNotEqual(transform["output"]["sha256"], original["sha256"])
        self.assertEqual(result["comparison"]["inputs"]["capture"]["sha256"], transform["output"]["sha256"])
        self.assertEqual(transform, json.loads((self.output / "transform.json").read_text(encoding="utf-8")))
        self.assertEqual(file_record(self.capture), original)
        with Image.open(normalized) as image:
            self.assertEqual(image.size, (72, 96))

    def test_direct_comparison_api_grants_no_quality_gate(self):
        result = compare_capture(self.baseline, self.capture, self.output, state_id="profile-card")
        self.assertEqual(result["status"], "passed")
        self.assert_image_only(result)
        self.assertFalse(result["diagnostic"])

    def test_out_of_bounds_roi_is_an_input_error(self):
        regions = {"viewport": {"width": 72, "height": 96}, "masks": [],
                   "required_rois": [{"id": "actions", "x": 60, "y": 76, "width": 20, "height": 12}]}
        path = self.root / "regions.json"
        path.write_text(json.dumps(regions), encoding="utf-8")
        completed = self.compare_cli("--regions", path)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("ROI rectangle exceeds viewport", json.loads(completed.stderr)["error"])
        self.assertFalse(self.output.exists())
        with self.assertRaisesRegex(ValueError, "ROI rectangle exceeds viewport"):
            compare_capture(self.baseline, self.capture, self.output, state_id="profile-card", regions=regions)

    def test_existing_comparison_directory_is_not_reused(self):
        self.output.mkdir()
        marker = self.output / "comparison.json"
        marker.write_text("existing report", encoding="utf-8")
        completed = self.compare_cli()
        self.assertEqual(completed.returncode, 2)
        self.assertIn("must be new", json.loads(completed.stderr)["error"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "existing report")
        self.assertEqual(list(self.output.iterdir()), [marker])

    def test_dangling_symlink_output_is_rejected_before_target_creation(self):
        target = self.root / "missing-target"
        self.output.symlink_to(target, target_is_directory=True)
        for resize in (False, True):
            with self.assertRaisesRegex(ValueError, "must be new"):
                compare_capture(self.baseline, self.capture, self.output, state_id="profile-card",
                                diagnostic=resize, resize_to_baseline=resize)
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(target.exists())

    def test_capabilities_advertise_bundled_visual_comparison_and_capture(self):
        completed = self.cli("capabilities")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        capabilities = json.loads(completed.stdout)
        self.assertTrue(capabilities["deterministic_visual_comparison"])
        self.assertTrue(capabilities["native_screen_capture"])
        self.assertIn("compare-visual", capabilities["commands"])
        self.assertIn("capture-screen", capabilities["commands"])

    def test_capture_cli_forwards_device_and_foreground_arguments_without_a_run(self):
        expected = {"format": "migration-screen-capture/1", "package_attested": False}
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("capture.capture_screen", return_value=expected) as capture:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = migration.main(["capture-screen", "--hdc", "/tools/hdc", "--target", "device-123",
                                            "--bundle", "com.example.live", "--output-dir", str(self.output)])
        self.assertEqual(exit_code, 0)
        capture.assert_called_once_with("/tools/hdc", str(self.output), target="device-123", bundle="com.example.live")
        self.assertEqual(json.loads(stdout.getvalue()), expected)
        self.assertEqual(stderr.getvalue(), "")

    def test_capture_cli_requires_explicit_bundle_before_calling_device_adapter(self):
        with patch("capture.capture_screen") as capture, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                migration.main(["capture-screen", "--hdc", "/tools/hdc", "--output-dir", str(self.output)])
        self.assertEqual(error.exception.code, 2)
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
