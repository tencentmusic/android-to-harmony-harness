"""Image evidence must decode, not merely start with a familiar signature."""
import sys
import base64
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contract import baseline_artifact
from engine import screenshot
from foundation import file_record


class ImageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_signature_only_device_evidence_is_rejected(self):
        for name, header in (("fake.png", b"\x89PNG\r\n\x1a\n"), ("fake.jpg", b"\xff\xd8\xff")):
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(header + b"not an image")
                with self.assertRaisesRegex(ValueError, "decode"):
                    screenshot(file_record(path))

    def test_signature_only_review_baseline_is_rejected(self):
        path = self.root / "fake.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\nnot an image")
        with self.assertRaisesRegex(ValueError, "decode"):
            baseline_artifact({"root": "android", "path": path.name, "locator": "Broken baseline"},
                              {"android": str(self.root)})

    def test_png_and_jpeg_evidence_remain_supported(self):
        for name in ("screen.png", "screen.jpg"):
            path = self.root / name
            Image.new("RGB", (16, 16), "white").save(path)
            self.assertTrue(screenshot(file_record(path)))
            record = baseline_artifact({"root": "android", "path": name, "locator": "Decoded image"},
                                       {"android": str(self.root)})
            self.assertEqual(record["sha256"], file_record(path)["sha256"])

    def test_bad_png_checksum_is_rejected_even_when_pixels_can_decode(self):
        path = self.root / "bad-checksum.png"
        path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jB1kAAAAASUVORK5CYII="))
        with self.assertRaisesRegex(ValueError, "decode"):
            screenshot(file_record(path))


if __name__ == "__main__":
    unittest.main()
