"""Read-only Harmony screenshot capture with device and foreground evidence."""
import json
import os
import re
import subprocess
import time
import uuid
import warnings
from pathlib import Path

import runtime
from foundation import file_record, read_json, require, text

FORMAT = "migration-screen-capture/1"
TIMEOUT_SECONDS = 30


def _foreground_bundles(output):
    current, foreground = None, set()
    for line in output.splitlines():
        if re.search(r"\b(?:Mission|AbilityRecord) ID #", line):
            current = None
        match = re.search(r"\bbundle\s+name\s*\[([^\]]+)\]", line)
        if match:
            current = match.group(1).strip()
        if current and re.match(r"\s*#?state\s+#FOREGROUND\b", line):
            foreground.add(current)
    return sorted(foreground)


def _targets(output):
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if lines == ["[Empty]"]:
        return []
    require(all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:\[\]-]*", line) for line in lines),
            "Cannot parse hdc target list")
    require(len(lines) == len(set(lines)), "Duplicate devices in hdc target list")
    return lines


def _image_record(path):
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError("Screen capture requires Pillow to validate the received image") from exc
    record = file_record(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                require(image.format == "PNG", "Received screenshot must be PNG")
                require(getattr(image, "n_frames", 1) == 1, "Received screenshot must have one frame")
                image.load()
                require(image.width > 0 and image.height > 0, "Received screenshot has empty dimensions")
                record.update(width=image.width, height=image.height)
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Cannot decode received screenshot") from exc
    require(file_record(path)["sha256"] == record["sha256"], "Screenshot changed while decoding")
    return record


def _hierarchy_record(path):
    record = file_record(path)
    layout = read_json(path)
    require(isinstance(layout, dict) and isinstance(layout.get("attributes"), dict)
            and layout["attributes"] and isinstance(layout.get("children"), list),
            "Received hierarchy is not a non-empty uitest layout")
    require(file_record(path) == record, "Hierarchy changed while parsing")
    return record


def capture_screen(hdc_path, output_dir, *, target=None, bundle=None):
    """Capture a fresh screen without navigating, installing or attesting a package.

    The output directory must not exist. Failures retain diagnostics and command
    output there; an existing directory is never reused or modified.
    """
    directory = Path(output_dir)
    require(not directory.exists() and not directory.is_symlink(),
            "Screen capture output directory must be new")
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ValueError("Screen capture output directory must be new") from exc
    directory = directory.resolve(strict=True)
    diagnostics_path = directory / "diagnostics.json"
    command_log = directory / "commands.log"
    command_log.write_text("", encoding="utf-8")
    diagnostics = {"format": "migration-screen-capture-diagnostics/1", "status": "running",
                   "started_at": time.time(), "commands": [], "package_attested": False}
    runtime.write_json(diagnostics_path, diagnostics)

    def run(step, arguments):
        argv = [str(hdc), *arguments]
        receipt = {"step": step, "argv": argv, "started_at": time.time()}
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
            receipt.update(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
        except subprocess.TimeoutExpired as exc:
            def decoded(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            receipt.update(exit_code=None, timed_out=True, stdout=decoded(exc.stdout), stderr=decoded(exc.stderr))
            raise ValueError("Screen capture command timed out: " + step) from exc
        except OSError as exc:
            receipt.update(exit_code=None, error=str(exc))
            raise ValueError("Cannot execute screen capture command: " + step) from exc
        finally:
            receipt["finished_at"] = time.time()
            diagnostics["commands"].append(receipt)
            with command_log.open("a", encoding="utf-8") as output:
                output.write(json.dumps(receipt, ensure_ascii=True) + "\n")
            runtime.write_json(diagnostics_path, diagnostics)
        require(result.returncode == 0, "Screen capture command failed: " + step)
        return result.stdout

    def foreground(stage):
        output = run("foreground-" + stage, ["-t", selected, "shell", "aa", "dump", "-l"])
        evidence = directory / ("foreground-" + stage + ".txt")
        evidence.write_text(output, encoding="utf-8")
        bundles = _foreground_bundles(output)
        require(bundles, "Cannot determine foreground application: " + stage)
        require(bundle is None or bundle in bundles, "Requested bundle is not FOREGROUND: " + stage)
        return {"bundles": bundles, "evidence": file_record(evidence)}

    try:
        hdc = Path(hdc_path)
        require(hdc.is_absolute() and hdc.is_file() and os.access(hdc, os.X_OK),
                "hdc must be an absolute executable file")
        require(target is None or (text(target) and not re.search(r"\s", target)), "Invalid device target")
        require(bundle is None or (isinstance(bundle, str)
                and re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", bundle)), "Invalid application bundle")
        devices = _targets(run("list-targets", ["list", "targets"]))
        if target is None:
            require(len(devices) == 1, "Automatic capture requires exactly one connected device")
            selected = devices[0]
        else:
            require(target in devices, "Requested device target is not connected")
            selected = target
        diagnostics.update(device_id=selected, requested_bundle=bundle)
        before = foreground("before")
        selected_bundle = bundle or (before["bundles"][0] if len(before["bundles"]) == 1 else None)
        stem = "/data/local/tmp/harness-capture-" + uuid.uuid4().hex
        remote_image, remote_layout = stem + ".png", stem + ".json"
        diagnostics["remote_files"] = {"image": remote_image, "hierarchy": remote_layout}
        captured_at = time.time()
        run("screenshot", ["-t", selected, "shell", "uitest", "screenCap", "-p", remote_image])
        image_path = directory / "screen.png"
        run("receive-screenshot", ["-t", selected, "file", "recv", remote_image, str(image_path)])
        image = _image_record(image_path)
        layout_args = ["-t", selected, "shell", "uitest", "dumpLayout", "-p", remote_layout]
        if selected_bundle:
            layout_args += ["-b", selected_bundle]
        run("hierarchy", layout_args)
        hierarchy_path = directory / "hierarchy.json"
        run("receive-hierarchy", ["-t", selected, "file", "recv", remote_layout, str(hierarchy_path)])
        hierarchy = _hierarchy_record(hierarchy_path)
        after = foreground("after")
        require(before["bundles"] == after["bundles"], "Foreground applications changed during capture")
        result = {"format": FORMAT, "device_id": selected, "bundle": selected_bundle,
                  "captured_at": captured_at, "completed_at": time.time(), "image": image,
                  "hierarchy": hierarchy, "foreground": {"before": before, "after": after},
                  "command_log": file_record(command_log), "package_attested": False}
        runtime.write_json(directory / "capture.json", result)
        diagnostics.update(status="captured", completed_at=time.time())
        runtime.write_json(diagnostics_path, diagnostics)
        return result
    except (ValueError, OSError, TypeError) as exc:
        diagnostics.update(status="failed", completed_at=time.time(),
                           failure={"category": "capture_failure", "reason": str(exc)})
        runtime.write_json(diagnostics_path, diagnostics)
        if isinstance(exc, ValueError):
            raise
        raise ValueError("Screen capture failed: " + str(exc)) from exc
