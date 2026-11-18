import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import foundation as base
from foundation import runtime
from visual_adapter import resolve_capture, run_adapter
from visual import verify_comparison_report


class VisualAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.android = self.root / "android"
        self.android.mkdir()
        y, x = np.indices((64, 64))
        self.pixels = np.repeat((((x // 4 + y // 4) % 2) * 180 + 30)[:, :, None], 3, axis=2).astype(np.uint8)
        self.image(self.android / "baseline.png")
        self.package = self.root / "current.hap"
        self.package.write_bytes(b"current package fixture")
        self.context = {"device_id": "phone-a", "system_version": "HarmonyOS", "app_id": "com.example.live",
                        "account_ref": "test-account", "normal_entry": True, "synthetic": False,
                        "package_sha256": base.file_record(self.package)["sha256"], "environment_digest": "environment-a"}
        self.dependency = self.native("device", "profile-device")
        self.spec = {"assertion_id": "pixels", "baseline_index": 0,
                     "capture_from": {"task_id": "device", "acceptance_id": "profile-device"}}
        self.request = {"format": "migration-harness/1", "demo": False,
            "task": {"id": "visual", "kind": "visual", "visual_comparison": {"profile-visual": self.spec}},
            "package": base.file_record(self.package), "roots": {"android": str(self.android)},
            "states": [{"id": "profile", "baselines": [{"root": "android", "path": "baseline.png", "locator": "screenshot"}]}],
            "acceptance": [{"id": "profile-visual", "kind": "visual", "state_id": "profile",
                            "assertions": [{"id": "pixels", "operator": "truthy", "expected": True}]}],
            "requested_acceptance_ids": ["profile-visual"], "dependency_receipts": {"device": self.dependency}}

    def image(self, path, pixels=None):
        Image.fromarray(self.pixels if pixels is None else pixels).save(path)
        return base.file_record(path)

    def native(self, tid, aid, context=None, pixels=None):
        directory = self.root / tid
        directory.mkdir()
        image = self.image(directory / "current.png", pixels)
        log = directory / "device.log"
        log.write_text("Device launch and screenshot capture fixture\n", encoding="utf-8")
        evidence = [image, base.file_record(log)]
        inputs = {"format": "migration-harness/1", "task": {"id": tid, "kind": "device"}}
        receipt = {"format": "migration-harness/1", "task_id": tid, "kind": "device", "inputs": inputs,
                   "input_digest": runtime.digest(inputs), "accepted": True, "context": context or self.context,
                   "point_results": [{"acceptance_id": aid, "status": "passed", "evidence": evidence}], "evidence": evidence}
        path = directory / "receipt.json"
        runtime.write_json(path, receipt)
        return base.file_record(path)

    def mutate_receipt(self, mutate, tid="device"):
        record = self.request["dependency_receipts"][tid]
        value = base.read_json(record["path"])
        mutate(value)
        runtime.write_json(record["path"], value)
        self.request["dependency_receipts"][tid] = base.file_record(record["path"])

    def execute(self, name="attempt"):
        directory = self.root / name
        directory.mkdir()
        request_path = directory / "request.json"
        runtime.write_json(request_path, self.request)
        raw = run_adapter(request_path, directory / "result.json")
        return raw, directory

    def test_resolve_uses_exact_upstream_context_and_unique_capture(self):
        native = resolve_capture(self.request, self.spec)
        self.assertEqual(native["context"], self.context)
        self.assertEqual(native["capture"], base.file_record(self.root / "device" / "current.png"))
        self.assertIn(self.dependency, native["evidence"])
        self.assertEqual(native["dependency_receipt"], self.dependency)
        native["context"]["device_id"] = "modified"
        self.assertEqual(resolve_capture(self.request, self.spec)["context"]["device_id"], "phone-a")

    def test_adapter_compares_real_images_and_preserves_all_evidence(self):
        raw, directory = self.execute()
        self.assertEqual(raw["context"], self.context)
        point = raw["points"][0]
        self.assertEqual(point["status"], "checked")
        self.assertEqual(point["observations"], [{"assertion_id": "pixels", "actual": True}])
        self.assertEqual(point["visual_comparison"], base.file_record(directory / "visual" / "profile-visual" / "comparison.json"))
        report = verify_comparison_report(point["visual_comparison"]["path"])
        expected = [*report["inputs"].values(), *report["artifacts"].values(), point["visual_comparison"], self.dependency]
        self.assertTrue(all(record["path"] in point["evidence"] for record in expected))
        self.assertTrue(any(path.endswith("device.log") for path in point["evidence"]))
        self.assertGreater(raw["phase_seconds"]["compare"], 0.)

    def test_image_difference_is_checked_false_and_not_precondition_failure(self):
        self.request["dependency_receipts"]["other-device"] = self.native("other-device", "other-point", pixels=np.roll(self.pixels, 3, axis=1))
        self.spec["capture_from"] = {"task_id": "other-device", "acceptance_id": "other-point"}
        raw, _ = self.execute()
        self.assertEqual(raw["points"][0]["status"], "checked")
        self.assertIs(raw["points"][0]["observations"][0]["actual"], False)

    def test_unrelated_assertions_are_not_fabricated(self):
        self.request["acceptance"][0]["assertions"].append({"id": "semantic", "operator": "truthy", "expected": True})
        raw, _ = self.execute()
        self.assertEqual([o["assertion_id"] for o in raw["points"][0]["observations"]], ["pixels"])

    def test_non_dependency_and_arbitrary_capture_paths_rejected(self):
        self.spec["capture_from"]["task_id"] = "not-a-dependency"
        with self.assertRaisesRegex(ValueError, "explicit current dependency"):
            resolve_capture(self.request, self.spec)
        self.spec["capture_from"] = {"task_id": "device", "acceptance_id": "profile-device", "path": str(self.android / "baseline.png")}
        with self.assertRaisesRegex(ValueError, "Unknown native capture"):
            resolve_capture(self.request, self.spec)

    def test_changed_dependency_receipt_or_evidence_rejected(self):
        original = Path(self.dependency["path"]).read_bytes()
        Path(self.dependency["path"]).write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Changed evidence"):
            resolve_capture(self.request, self.spec)
        Path(self.dependency["path"]).write_bytes(original)
        (self.root / "device" / "device.log").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Changed evidence"):
            resolve_capture(self.request, self.spec)

    def test_wrong_receipt_kind_identity_format_or_digest_rejected(self):
        for mutate in (lambda r: r.update(kind="visual"), lambda r: r.update(task_id="another"),
                       lambda r: r.update(format="old"), lambda r: r.update(input_digest="stale"),
                       lambda r: r["inputs"].update(format="old")):
            original = Path(self.dependency["path"]).read_bytes()
            self.mutate_receipt(mutate)
            with self.assertRaises(ValueError):
                resolve_capture(self.request, self.spec)
            Path(self.dependency["path"]).write_bytes(original)
            self.request["dependency_receipts"]["device"] = base.file_record(self.dependency["path"])

    def test_unpassed_point_or_receipt_or_wrong_package_rejected(self):
        for mutate in (lambda r: r.update(accepted=False), lambda r: r["point_results"][0].update(status="failed"),
                       lambda r: r["context"].update(package_sha256="old-package"),
                       lambda r: r["context"].update(normal_entry=False), lambda r: r["context"].update(synthetic=True)):
            original = Path(self.dependency["path"]).read_bytes()
            self.mutate_receipt(mutate)
            with self.assertRaises(ValueError):
                resolve_capture(self.request, self.spec)
            Path(self.dependency["path"]).write_bytes(original)
            self.request["dependency_receipts"]["device"] = base.file_record(self.dependency["path"])

    def test_ambiguous_images_require_a_unique_basename(self):
        extra = self.image(self.root / "device" / "second.png")
        def add_extra(receipt):
            receipt["point_results"][0]["evidence"].append(extra)
            receipt["evidence"].append(extra)
        self.mutate_receipt(add_extra)
        with self.assertRaisesRegex(ValueError, "one unique"):
            resolve_capture(self.request, self.spec)
        self.spec["capture_from"]["image_name"] = "current.png"
        self.assertEqual(Path(resolve_capture(self.request, self.spec)["capture"]["path"]).name, "current.png")
        self.spec["capture_from"]["image_name"] = "../current.png"
        with self.assertRaisesRegex(ValueError, "basename"):
            resolve_capture(self.request, self.spec)

    def test_missing_baseline_and_dimension_mismatch_are_blocked_without_observations(self):
        self.request["states"][0]["baselines"] = []
        raw, _ = self.execute("missing-baseline")
        self.assertEqual(raw["points"][0]["status"], "blocked")
        self.assertNotIn("observations", raw["points"][0])
        self.request["states"][0]["baselines"] = [{"root": "android", "path": "baseline.png", "locator": "screenshot"}]
        self.image(self.android / "baseline.png", self.pixels[:60])
        raw, _ = self.execute("wrong-dimensions")
        self.assertEqual(raw["points"][0]["status"], "blocked")
        self.assertIn("dimensions differ", raw["points"][0]["reason"])
        self.assertNotIn("observations", raw["points"][0])

    def test_missing_source_and_invalid_region_are_blocked_without_observations(self):
        (self.android / "baseline.png").unlink()
        raw, _ = self.execute("missing-source")
        self.assertEqual(raw["points"][0]["status"], "blocked")
        self.assertNotIn("observations", raw["points"][0])
        self.image(self.android / "baseline.png")
        self.spec["regions"] = {"viewport": {"width": 64, "height": 64}, "masks": [],
                               "required_rois": [{"id": "button", "x": -1, "y": 0, "width": 10, "height": 10}]}
        raw, _ = self.execute("invalid-region")
        self.assertEqual(raw["points"][0]["status"], "blocked")
        self.assertNotIn("observations", raw["points"][0])

    def test_grouped_state_and_unknown_binding_are_blocked(self):
        self.request["acceptance"][0]["covered_state_ids"] = ["profile", "another"]
        raw, _ = self.execute("grouped")
        self.assertEqual(raw["points"][0]["status"], "blocked")
        self.assertIn("one covered state", raw["points"][0]["reason"])
        self.request["acceptance"][0].pop("covered_state_ids")
        self.spec["capture_path"] = str(self.root / "device" / "current.png")
        raw, _ = self.execute("unknown-binding")
        self.assertEqual(raw["points"][0]["status"], "blocked")
        self.assertIn("Unknown visual comparison binding", raw["points"][0]["reason"])

    def test_one_blocked_point_does_not_prevent_other_points(self):
        self.request["requested_acceptance_ids"].insert(0, "bad-visual")
        self.request["acceptance"].append({**self.request["acceptance"][0], "id": "bad-visual"})
        self.request["task"]["visual_comparison"]["bad-visual"] = {**self.spec, "baseline_index": 3}
        raw, _ = self.execute()
        self.assertEqual([point["status"] for point in raw["points"]], ["blocked", "checked"])

    def test_different_native_context_blocks_mismatched_point(self):
        other = self.native("device-other", "other-point", context={**self.context, "account_ref": "different-account"})
        self.request["dependency_receipts"]["device-other"] = other
        self.request["acceptance"].append({**self.request["acceptance"][0], "id": "other-visual"})
        self.request["requested_acceptance_ids"].append("other-visual")
        self.request["task"]["visual_comparison"]["other-visual"] = {**self.spec,
            "capture_from": {"task_id": "device-other", "acceptance_id": "other-point"}}
        raw, _ = self.execute()
        self.assertEqual(raw["context"], self.context)
        self.assertEqual([point["status"] for point in raw["points"]], ["checked", "blocked"])
        self.assertIn("different native capture contexts", raw["points"][1]["reason"])

    def test_cli_uses_request_and_result_protocol(self):
        directory = self.root / "cli"
        directory.mkdir()
        runtime.write_json(directory / "request.json", self.request)
        process = subprocess.run([sys.executable, str(ROOT / "visual_adapter.py"), str(directory / "request.json"),
                                  str(directory / "result.json")], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(base.read_json(directory / "result.json")["points"][0]["status"], "checked")


if __name__ == "__main__":
    unittest.main()
