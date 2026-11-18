import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import runtime
from visual import compare_images, verify_comparison_report, _measure


class VisualComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        y, x = np.indices((128, 128))
        self.pattern = np.repeat((((x // 4 + y // 4) % 2) * 180 + 30)[:, :, None], 3, axis=2).astype(np.uint8)
        self.counter = 0

    def image(self, name, pixels, **options):
        path = self.root / name
        Image.fromarray(pixels).save(path, **options)
        return path

    def compare(self, left=None, right=None, **kwargs):
        self.counter += 1
        a = self.image("baseline-" + str(self.counter) + ".png", self.pattern if left is None else left)
        b = self.image("capture-" + str(self.counter) + ".png", self.pattern if right is None else right)
        return compare_images(a, b, self.root / ("result-" + str(self.counter)), state_id="profile", **kwargs)

    def regions(self, rois=None, masks=None):
        return {"viewport": {"width": 128, "height": 128}, "masks": masks or [],
                "required_rois": rois or [{"id": "card", "x": 0, "y": 0, "width": 128, "height": 128}]}

    def test_identical_real_pngs_generate_verifiable_artifacts(self):
        report = self.compare()
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["full"]["score"], 1.)
        self.assertEqual(report["full"]["normalized_mae"], 0.)
        self.assertEqual(report["thresholds"], {"minimum_ssim": .85, "maximum_normalized_mae": .12})
        self.assertTrue(report["gate_eligible"])
        self.assertEqual(report["acceptance_scope"], "image_comparison_only")
        self.assertEqual(set(report["dependencies"]), {"Pillow", "numpy", "scikit-image"})
        output = Path(report["artifacts"]["comparison"]["path"]).parent
        self.assertEqual(verify_comparison_report(output / "comparison.json"), report)
        for record in report["artifacts"].values():
            with Image.open(record["path"]) as artifact:
                artifact.verify()

    def test_structural_displacement_fails_ssim(self):
        report = self.compare(right=np.roll(self.pattern, 3, axis=1))
        self.assertEqual(report["status"], "failed")
        self.assertLess(report["full"]["score"], .85)

    def test_uniform_brightness_error_fails_mae_even_with_good_ssim(self):
        report = self.compare(np.full_like(self.pattern, 160), np.full_like(self.pattern, 200))
        self.assertGreater(report["full"]["score"], .85)
        self.assertGreater(report["full"]["normalized_mae"], .12)
        self.assertEqual(report["status"], "failed")

    def test_masked_motion_does_not_leak_into_surrounding_windows(self):
        right = self.pattern.copy()
        right[30:80, 30:80] = 255 - right[30:80, 30:80]
        mask = {"id": "video", "x": 30, "y": 30, "width": 50, "height": 50, "reason": "Live video"}
        report = self.compare(right=right, regions=self.regions(masks=[mask]))
        self.assertEqual(report["full"]["score"], 1.)
        self.assertEqual(report["full"]["normalized_mae"], 0.)
        self.assertLess(report["full"]["ssim_windows"], (128 - 6) ** 2 - 50 ** 2)
        self.assertEqual(report["status"], "passed")

    def test_required_roi_cannot_be_hidden_by_full_screen_average(self):
        left = np.full_like(self.pattern, 128)
        right = left.copy()
        right[32:48, 32:48] = 0
        roi = {"id": "icon", "x": 32, "y": 32, "width": 16, "height": 16}
        report = self.compare(left, right, regions=self.regions([roi]))
        self.assertEqual(report["full"]["status"], "passed")
        self.assertEqual(report["required_rois"][0]["status"], "failed")
        self.assertEqual(report["status"], "failed")

    def test_diagnostic_scores_never_become_gate_eligible(self):
        report = self.compare(diagnostic=True)
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["gate_eligible"])
        self.assertTrue(report["diagnostic"])

    def test_true_jpeg_and_png_are_decoded_without_extension_guessing(self):
        baseline = self.image("reference.png", np.full_like(self.pattern, 150))
        capture = self.image("capture.bytes", np.full_like(self.pattern, 150), format="JPEG", quality=100)
        report = compare_images(baseline, capture, self.root / "jpeg-output", state_id="jpeg")
        self.assertEqual(report["inputs"]["capture"]["format"], "JPEG")
        self.assertEqual(report["status"], "passed")

    def test_dimensions_are_never_implicitly_resized(self):
        with self.assertRaisesRegex(ValueError, "dimensions differ"):
            self.compare(right=self.pattern[:127])

    def test_invalid_or_empty_regions_rejected(self):
        invalid = [None, {}, {"id": "roi", "x": -1, "y": 0, "width": 20, "height": 20},
                   {"id": "roi", "x": 0, "y": 0, "width": 129, "height": 20},
                   {"id": "roi", "x": .5, "y": 0, "width": 20, "height": 20},
                   {"id": "roi", "x": True, "y": 0, "width": 20, "height": 20},
                   {"id": "roi", "x": 0, "y": 0, "width": 0, "height": 20}]
        for roi in invalid:
            with self.subTest(roi=roi), self.assertRaises(ValueError):
                self.compare(regions=self.regions([roi]))
        for mutate in (lambda r: r.update(required_rois=[]), lambda r: r["viewport"].update(width=127),
                       lambda r: r.update(resize={"width": 128}), lambda r: r.update(masks={})):
            regions = self.regions()
            mutate(regions)
            with self.assertRaises(ValueError):
                self.compare(regions=regions)

    def test_duplicate_roi_ids_and_too_small_rois_rejected(self):
        roi = {"id": "button", "x": 0, "y": 0, "width": 20, "height": 20}
        with self.assertRaisesRegex(ValueError, "Duplicate ROI"):
            self.compare(regions=self.regions([roi, roi]))
        with self.assertRaisesRegex(ValueError, "too small"):
            self.compare(regions=self.regions([{**roi, "width": 2}]))

    def test_masks_require_reason_and_comparable_area(self):
        mask = {"id": "video", "x": 0, "y": 0, "width": 128, "height": 128, "reason": "Motion"}
        with self.assertRaisesRegex(ValueError, "5% comparable"):
            self.compare(regions=self.regions(masks=[mask]))
        with self.assertRaisesRegex(ValueError, "require a reason"):
            self.compare(regions=self.regions(masks=[{**mask, "reason": ""}]))
        with self.assertRaisesRegex(ValueError, "exceeds viewport"):
            self.compare(regions=self.regions(masks=[{**mask, "height": 129}]))

    def test_roi_fully_masked_or_without_intact_window_rejected(self):
        roi = {"id": "video-card", "x": 20, "y": 20, "width": 20, "height": 20}
        mask = {**roi, "id": "video", "reason": "Motion"}
        with self.assertRaisesRegex(ValueError, "no unmasked pixels"):
            self.compare(regions=self.regions([roi], [mask]))
        stripes = [{"id": "video-" + str(x), "x": x, "y": 0, "width": 2, "height": 128,
                    "reason": "Motion"} for x in range(0, 128, 4)]
        with self.assertRaisesRegex(ValueError, "no fully unmasked SSIM window"):
            self.compare(regions=self.regions(masks=stripes))

    def test_bad_images_and_transparency_rejected(self):
        good = self.image("good.png", self.pattern)
        bad = self.root / "bad.png"
        bad.write_bytes(b"not a PNG")
        with self.assertRaisesRegex(ValueError, "Cannot decode"):
            compare_images(good, bad, self.root / "bad-result", state_id="bad")
        rgba = np.concatenate([self.pattern, np.full((128, 128, 1), 127, dtype=np.uint8)], axis=2)
        bad = self.image("transparent.png", rgba)
        with self.assertRaisesRegex(ValueError, "opaque"):
            compare_images(good, bad, self.root / "transparent-result", state_id="bad")

    def test_nonfinite_pixels_and_metrics_rejected(self):
        pixels = self.pattern.astype(float)
        mask = np.ones((128, 128), dtype=bool)
        pixels[0, 0, 0] = float("nan")
        with self.assertRaisesRegex(ValueError, "Non-finite visual pixels"):
            _measure(pixels, pixels, mask, np, None)
        pixels = self.pattern.astype(float)
        with self.assertRaisesRegex(ValueError, "Non-finite visual metrics"):
            _measure(pixels, pixels, mask, np, lambda *a, **k: (None, np.full_like(pixels, float("inf"))))

    def test_output_directory_must_be_new(self):
        path = self.image("input.png", self.pattern)
        original = path.read_bytes()
        for output in (self.root, path):
            with self.assertRaisesRegex(ValueError, "must be new"):
                compare_images(path, path, output, state_id="same")
        self.assertEqual(path.read_bytes(), original)

    def test_changed_input_and_artifact_are_rejected(self):
        report = self.compare()
        output = Path(report["artifacts"]["comparison"]["path"]).parent
        self.image(Path(report["inputs"]["capture"]["path"]).name, 255 - self.pattern)
        with self.assertRaisesRegex(ValueError, "recomputed pixels"):
            verify_comparison_report(output / "comparison.json")
        report = self.compare()
        output = Path(report["artifacts"]["comparison"]["path"]).parent
        Path(report["artifacts"]["difference"]["path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Changed visual comparison artifact"):
            verify_comparison_report(output / "comparison.json")

    def test_fabricated_scores_are_recomputed_even_after_rehashing_report(self):
        report = self.compare(right=np.roll(self.pattern, 3, axis=1))
        output = Path(report["artifacts"]["comparison"]["path"]).parent
        fake = copy.deepcopy(report)
        fake["status"] = "passed"
        fake["full"].update(score=1., normalized_mae=0., status="passed")
        fake["report_digest"] = runtime.digest({key: value for key, value in fake.items() if key != "report_digest"})
        (output / "comparison.json").write_text(json.dumps(fake), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "recomputed pixels"):
            verify_comparison_report(output / "comparison.json")

    def test_fabricated_heatmap_is_recomputed_even_after_rehashing_artifacts(self):
        report = self.compare(right=np.roll(self.pattern, 3, axis=1))
        output = Path(report["artifacts"]["comparison"]["path"]).parent
        path = output / "difference.png"
        Image.fromarray(np.zeros_like(self.pattern)).save(path)
        report["artifacts"]["difference"]["sha256"] = runtime.file_hash(path)
        report["report_digest"] = runtime.digest({key: value for key, value in report.items() if key != "report_digest"})
        (output / "comparison.json").write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "artifact differs from recomputed pixels"):
            verify_comparison_report(output / "comparison.json")


if __name__ == "__main__":
    unittest.main()
