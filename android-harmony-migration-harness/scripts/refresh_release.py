#!/usr/bin/env python3
"""Refresh the manifest of the single maintained package; never install it."""
import json
import subprocess
import sys
from pathlib import Path

from common import ROOT, read, sha


def write_release(path, value, newline="\n", sort_keys=True):
    pending = path.with_suffix(path.suffix + ".refresh-tmp")
    try:
        with pending.open("w", encoding="utf-8", newline=newline) as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=sort_keys) + "\n")
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def refresh(root=ROOT):
    root = Path(root).resolve()
    repository = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=root, text=True).strip())
    runtime = root / "runtime/harness"
    if not (runtime / "engine.py").is_file() or {p.name for p in (root / "runtime").iterdir() if p.is_dir()} != {"harness"}:
        raise ValueError("Release requires exactly one runtime: runtime/harness")
    result = subprocess.run([sys.executable, str(root / "scripts/harness.py"), "capabilities"],
                            capture_output=True, encoding="utf-8", check=True)
    capabilities = json.loads(result.stdout)
    write_release(root / "release-capabilities.json", capabilities)
    engine_files = {p.relative_to(root).as_posix(): sha(p) for p in sorted(runtime.glob("*.py"))}
    provenance_path = root / "source-provenance.json"
    provenance = read(provenance_path)
    provenance.update(core_engine_modified=True, maintenance_source="runtime/harness in androidtoharmonyharness Git checkout",
                      maintenance_contract="Local changes -> tests -> MR to master -> consumer pull/install",
                      current_engine_files=engine_files)
    write_release(provenance_path, provenance, sort_keys=False)
    relative = root.relative_to(repository)
    listed = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", str(relative)], cwd=repository)
    paths = sorted({Path(p.decode("utf-8")).relative_to(relative) for p in listed.split(b"\0") if p})
    paths = [p for p in paths if p.name != "release-files.json" and p.suffix != ".pyc"
             and not set(p.parts) & {".venv", ".local", "__pycache__"} and (root / p).is_file()]
    if any((root / p).is_symlink() for p in paths):
        raise ValueError("Release cannot contain symlinks")
    manifest = {p.as_posix(): sha(root / p) for p in paths}
    write_release(root / "release-files.json", manifest, newline="\r\n")
    return {"files": len(manifest), "engine_modules": len(engine_files),
            "commands": len(capabilities["commands"]), "installed": False}


if __name__ == "__main__":
    print(json.dumps(refresh(), ensure_ascii=False, indent=2))
