"""Portable Agent bridge protocol; inspection never executes the configured Agent."""
import json
import math
import os
from pathlib import Path
import shutil
import sys

FORMAT = "harness-agent-adapter/1"
TURN_FORMAT = "harness-agent-turn/1"
EVENT_FORMAT = "harness-agent-event/1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_adapter(path):
    path = Path(path).expanduser().resolve(strict=True)
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict) and value.get("format") == FORMAT, "Invalid Agent adapter format")
    require(isinstance(value.get("id"), str) and value["id"].strip(), "Agent adapter needs an ID")
    require(value.get("mode") in {"managed", "host_hook", "cooperative"}, "Unknown Agent integration mode")
    value = dict(value, descriptor=str(path))
    if value["mode"] != "managed":
        return value
    argv = value.get("command")
    require(isinstance(argv, list) and argv and all(isinstance(a, str) and a and "\0" not in a for a in argv),
            "Agent command must be an argv array")
    require("{request}" in argv and "{result}" in argv, "Agent command needs standalone {request} and {result} arguments")
    cwd = value.get("cwd", ".")
    require(isinstance(cwd, str) and cwd, "Agent cwd must be a directory")
    cwd = (path.parent / cwd).resolve(strict=True)
    require(cwd.is_dir(), "Agent cwd must be a directory")
    value["cwd"] = str(cwd)
    value["command"] = [sys.executable if a == "{python}" else a for a in argv]
    executable = value["command"][0]
    if Path(executable).is_absolute() or "/" in executable or "\\" in executable:
        executable = str((cwd / executable).resolve(strict=True))
        require(Path(executable).is_file() and (os.name == "nt" or os.access(executable, os.X_OK)), "Agent executable is unavailable")
    else:
        executable = shutil.which(executable)
        require(executable, "Agent executable is unavailable")
    require(os.name != "nt" or Path(executable).suffix.lower() not in {".bat", ".cmd"},
            "Use a native executable or interpreter instead of a batch wrapper")
    value["command"][0] = executable
    for key, default in (("timeout_seconds", 600), ("idle_timeout_seconds", 300)):
        number = value.get(key, default)
        require(type(number) in (int, float) and math.isfinite(number) and number > 0, "Invalid Agent " + key)
        value[key] = number
    require(type(value.get("supports_resume", False)) is bool, "supports_resume must be boolean")
    value.setdefault("supports_resume", False)
    return value


def inspect_adapter(path, require_enforcement=False):
    adapter = load_adapter(path)
    managed = adapter["mode"] == "managed"
    return {"format": FORMAT, "adapter_id": adapter["id"], "mode": adapter["mode"],
            "status": "ready" if managed else "enforcement_unavailable" if require_enforcement else "declared_only",
            "enforcement_available": managed, "active_enforcement": False,
            "enforcement_scope": "agent-drive_invocations" if managed else "flow_only" if adapter["mode"] == "cooperative" else "external_host_unverified",
            "resume_declared": adapter.get("supports_resume", False),
            "automatic_host_hook": False, "changes_performed": False,
            "note": "Managed enforcement applies only when agent-drive owns execution. Host hooks require the host to invoke end-check and honor its veto; a descriptor does not prove that wiring."}


def read_result(path, request):
    path = Path(path)
    require(path.stat().st_size <= 1048576, "Agent result exceeds 1 MiB")
    result = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(result, dict) and result.get("format") == TURN_FORMAT, "Invalid Agent turn result")
    require(all(result.get(k) == request[k] for k in ("turn_id", "run_id", "session_id")),
            "Agent result belongs to another turn, run or session")
    require(result.get("status") in {"yielded", "completed", "interrupted"}, "Invalid Agent result status")
    token = result.get("resume_token")
    require(token is None or isinstance(token, (str, dict)) and bool(token)
            and len(json.dumps(token).encode("utf-8")) <= 16384, "Invalid Agent resume token")
    return result


def read_events(path, request):
    path = Path(path)
    if not path.exists():
        return []
    require(path.stat().st_size <= 4194304, "Agent events exceed 4 MiB")
    body = path.read_text(encoding="utf-8")
    require(not body or body.endswith("\n"), "Agent event stream has an incomplete final record")
    lines = body.splitlines()
    require(len(lines) <= 10000, "Agent event count exceeds 10000")
    events = []
    for sequence, line in enumerate(lines, 1):
        event = json.loads(line)
        require(isinstance(event, dict) and event.get("format") == EVENT_FORMAT
                and type(event.get("sequence")) is int and event["sequence"] == sequence, "Invalid Agent event sequence")
        require(all(event.get(k) == request[k] for k in ("turn_id", "run_id", "session_id")), "Agent event belongs to another turn, run or session")
        require(event.get("type") in {"started", "progress", "checkpoint", "message", "interrupted", "completed"}
                and isinstance(event.get("data", {}), dict), "Invalid Agent event type or data")
        events.append(event)
    return events
