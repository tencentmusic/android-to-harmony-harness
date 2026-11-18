import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from capture import capture_screen
from foundation import file_record

BUNDLE = "com.example.live"
DEVICE = "device-123"


def foreground(bundle=BUNDLE, state="FOREGROUND"):
    return ("User ID #100\n  current mission lists:{\n"
            "    Mission ID #144  mission name #[#" + bundle + ":entry:EntryAbility]\n"
            "      AbilityRecord ID #2911\n        app name [" + bundle + "]\n"
            "        main name [EntryAbility]\n        bundle name [" + bundle + "]\n"
            "        ability type [PAGE]\n        state #" + state + "  start time [102195596]\n"
            "        app state #" + state + "\n  }\n")


class FakeHdc:
    def __init__(self, *, devices=DEVICE, before=None, after=None, image=None, layout=None,
                 missing=None, fail=None, timeout=None):
        self.devices = devices
        self.before = before if before is not None else foreground()
        self.after = after if after is not None else self.before
        pixels = io.BytesIO()
        Image.new("RGB", (24, 40), "green").save(pixels, format="PNG")
        self.image = pixels.getvalue() if image is None else image
        self.layout = {"attributes": {"type": "Root", "bounds": "[0,0][24,40]"}, "children": []} if layout is None else layout
        self.missing, self.fail, self.timeout = missing, fail, timeout
        self.calls, self.foreground_count = [], 0

    def __call__(self, argv, **kwargs):
        assert kwargs == {"capture_output": True, "text": True, "timeout": 30}
        self.calls.append(argv)
        operation = argv[3:] if argv[1:2] == ["-t"] else argv[1:]
        if self.timeout and self.timeout in operation:
            raise subprocess.TimeoutExpired(argv, 30, output=b"partial output", stderr=b"timed out")
        if self.fail and self.fail in operation:
            return subprocess.CompletedProcess(argv, 1, "", "device command failed")
        output = "success"
        if operation == ["list", "targets"]:
            output = self.devices + "\n"
        elif operation == ["shell", "aa", "dump", "-l"]:
            output = self.before if self.foreground_count == 0 else self.after
            self.foreground_count += 1
        elif operation[:2] == ["file", "recv"]:
            suffix = Path(operation[2]).suffix
            if suffix != self.missing:
                destination = Path(operation[3])
                if suffix == ".png":
                    destination.write_bytes(self.image)
                else:
                    destination.write_text(json.dumps(self.layout), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, output, "")


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hdc = self.root / "hdc with spaces"
        self.hdc.write_text("test executable", encoding="utf-8")
        self.hdc.chmod(0o755)
        self.output = self.root / "capture"

    def capture(self, fake, **kwargs):
        with patch("capture.subprocess.run", side_effect=fake):
            return capture_screen(self.hdc, self.output, **kwargs)

    def diagnostics(self):
        return json.loads((self.output / "diagnostics.json").read_text(encoding="utf-8"))

    def test_capture_has_decoded_image_layout_foreground_and_command_evidence(self):
        fake = FakeHdc()
        result = self.capture(fake, bundle=BUNDLE)
        self.assertEqual(result["format"], "migration-screen-capture/1")
        self.assertEqual(result["device_id"], DEVICE)
        self.assertEqual(result["bundle"], BUNDLE)
        self.assertEqual((result["image"]["width"], result["image"]["height"]), (24, 40))
        self.assertEqual(result["image"]["sha256"], file_record(self.output / "screen.png")["sha256"])
        self.assertFalse(result["package_attested"])
        self.assertNotIn("package_sha256", result)
        self.assertLessEqual(result["captured_at"], result["completed_at"])
        for stage in ("before", "after"):
            self.assertEqual(result["foreground"][stage]["bundles"], [BUNDLE])
            self.assertEqual(result["foreground"][stage]["evidence"], file_record(self.output / ("foreground-" + stage + ".txt")))
        self.assertEqual(result["hierarchy"], file_record(self.output / "hierarchy.json"))
        self.assertEqual(result["command_log"], file_record(self.output / "commands.log"))
        self.assertEqual(result, json.loads((self.output / "capture.json").read_text(encoding="utf-8")))
        self.assertEqual(self.diagnostics()["status"], "captured")
        commands = [json.loads(line) for line in (self.output / "commands.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(commands), 7)
        self.assertEqual(commands[4]["argv"][-2:], ["-b", BUNDLE])
        self.assertTrue(all(command[0] == str(self.hdc) for command in fake.calls))
        self.assertTrue(all(command[1:3] == ["-t", DEVICE] for command in fake.calls[1:]))
        self.assertFalse(any("install" in command or "start" in command or "click" in command for command in fake.calls))

    def test_explicit_target_selects_one_of_multiple_devices(self):
        result = self.capture(FakeHdc(devices="device-other\n" + DEVICE), target=DEVICE, bundle=BUNDLE)
        self.assertEqual(result["device_id"], DEVICE)

    def test_automatic_target_requires_exactly_one_device(self):
        for number, devices in enumerate(("[Empty]", DEVICE + "\nother-device", "")):
            with self.subTest(devices=devices):
                self.output = self.root / ("ambiguous-" + str(number))
                fake = FakeHdc(devices=devices)
                with self.assertRaisesRegex(ValueError, "exactly one"):
                    self.capture(fake)
                self.assertEqual(len(fake.calls), 1)
                self.assertEqual(self.diagnostics()["status"], "failed")

    def test_unknown_explicit_target_is_rejected(self):
        fake = FakeHdc()
        with self.assertRaisesRegex(ValueError, "not connected"):
            self.capture(fake, target="other-device")
        self.assertEqual(len(fake.calls), 1)

    def test_existing_output_is_never_modified(self):
        self.output.mkdir()
        sentinel = self.output / "capture.json"
        sentinel.write_text("original", encoding="utf-8")
        with patch("capture.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "must be new"):
                capture_screen(self.hdc, self.output)
        run.assert_not_called()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "original")
        self.assertEqual(list(self.output.iterdir()), [sentinel])

    def test_relative_or_nonexecutable_hdc_is_rejected_with_diagnostics(self):
        for number, tool in enumerate(("hdc", self.root, self.root / "missing")):
            with self.subTest(tool=tool):
                self.output = self.root / ("invalid-tool-" + str(number))
                with patch("capture.subprocess.run") as run:
                    with self.assertRaisesRegex(ValueError, "absolute executable"):
                        capture_screen(tool, self.output)
                run.assert_not_called()
                self.assertEqual(self.diagnostics()["status"], "failed")
        self.output = self.root / "not-executable"
        self.hdc.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "absolute executable"):
            capture_screen(self.hdc, self.output)

    def test_background_or_wrong_bundle_stops_before_capture(self):
        background = foreground(state="BACKGROUND")
        for number, output in enumerate((background, foreground("com.example.other"), foreground(state="FOREGROUNDING"),
                                         background.replace("app state #BACKGROUND", "app state #FOREGROUND"),
                                         background + "\nMission ID #145\n  state #FOREGROUND\n")):
            with self.subTest(output=output):
                self.output = self.root / ("wrong-foreground-" + str(number))
                fake = FakeHdc(before=output)
                with self.assertRaisesRegex(ValueError, "foreground|FOREGROUND"):
                    self.capture(fake, bundle=BUNDLE)
                self.assertEqual(len(fake.calls), 2)
                self.assertFalse((self.output / "screen.png").exists())

    def test_front_application_change_invalidates_capture(self):
        fake = FakeHdc(after=foreground("com.example.other"))
        with self.assertRaisesRegex(ValueError, "not FOREGROUND"):
            self.capture(fake, bundle=BUNDLE)
        self.assertTrue((self.output / "screen.png").exists())
        self.assertFalse((self.output / "capture.json").exists())
        self.assertEqual(self.diagnostics()["status"], "failed")

    def test_unspecified_bundle_is_derived_and_still_checked_for_change(self):
        result = self.capture(FakeHdc())
        self.assertEqual(result["bundle"], BUNDLE)
        self.output = self.root / "changed-without-bundle"
        with self.assertRaisesRegex(ValueError, "changed during capture"):
            self.capture(FakeHdc(after=foreground("com.example.other")))

    def test_zero_exit_with_missing_or_invalid_image_is_not_success(self):
        for number, fake in enumerate((FakeHdc(missing=".png"), FakeHdc(image=b""), FakeHdc(image=b"not a PNG"))):
            with self.subTest(number=number):
                self.output = self.root / ("bad-image-" + str(number))
                with self.assertRaises(ValueError):
                    self.capture(fake, bundle=BUNDLE)
                self.assertFalse((self.output / "capture.json").exists())
                self.assertEqual(self.diagnostics()["status"], "failed")

    def test_invalid_or_missing_hierarchy_is_not_success(self):
        for number, fake in enumerate((FakeHdc(missing=".json"), FakeHdc(layout={}), FakeHdc(layout=[]))):
            with self.subTest(number=number):
                self.output = self.root / ("bad-layout-" + str(number))
                with self.assertRaises(ValueError):
                    self.capture(fake, bundle=BUNDLE)
                self.assertFalse((self.output / "capture.json").exists())
                self.assertEqual(self.diagnostics()["status"], "failed")

    def test_command_failure_and_timeout_retain_command_diagnostics(self):
        for number, fake in enumerate((FakeHdc(fail="screenCap"), FakeHdc(timeout="screenCap"))):
            with self.subTest(number=number):
                self.output = self.root / ("failed-command-" + str(number))
                with self.assertRaisesRegex(ValueError, "command failed|timed out"):
                    self.capture(fake, bundle=BUNDLE)
                diagnostics = self.diagnostics()
                self.assertEqual(diagnostics["status"], "failed")
                self.assertEqual(diagnostics["commands"][-1]["step"], "screenshot")
                self.assertIn("stdout", diagnostics["commands"][-1])
                self.assertIn("stderr", diagnostics["commands"][-1])
                self.assertIn("screenCap", (self.output / "commands.log").read_text(encoding="utf-8"))

    def test_remote_paths_are_unique_for_each_capture(self):
        first = FakeHdc()
        self.capture(first, bundle=BUNDLE)
        self.output = self.root / "second-capture"
        second = FakeHdc()
        self.capture(second, bundle=BUNDLE)
        self.assertNotEqual(first.calls[2][-1], second.calls[2][-1])


if __name__ == "__main__":
    unittest.main()
