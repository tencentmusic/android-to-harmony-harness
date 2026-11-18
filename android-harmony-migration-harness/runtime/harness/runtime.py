"""Bounded local execution and derived progress; never grants a quality gate."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import errno
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

VERSION = 1


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def filesystem_path(path):
    path = Path(path)
    if os.name != "nt":
        return path
    value = str(path.absolute())
    if value.startswith("\\\\?\\"):
        return Path(value)
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


def file_hash(path):
    h = hashlib.sha256()
    with filesystem_path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def replace_file(source, target):
    # Windows can briefly deny replacement while a reader holds the target open.
    deadline = time.monotonic() + .5
    while True:
        try:
            filesystem_path(source).replace(filesystem_path(target))
            return
        except PermissionError:
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(.02)


def write_json(path, value):
    path = filesystem_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(tmp, path)
        if os.name == "posix":
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)


def load(path, default=None):
    try:
        return json.loads(filesystem_path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def process_options():
    return ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt" else {"start_new_session": True})


def executable_argv(argv):
    """Windows does not interpret Python shebangs; never pass batch arguments to cmd."""
    argv = [str(value) for value in argv]
    if os.name == "nt" and argv:
        suffix = Path(argv[0]).suffix.lower()
        if suffix in {".bat", ".cmd"}:
            raise ValueError("Use the native executable or node.exe + JavaScript entry, not a batch wrapper")
        if suffix == ".py":
            return [sys.executable, *argv]
    return argv


def _stop_process(process):
    # The child owns its process group: cancelling one build cannot kill others.
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        # /T targets only descendants of this still-running owned process.
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"
        stopped = subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5)
        if stopped.returncode and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
            raise ValueError("Windows process-tree cancellation failed; inspect the retained execution log")
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def run_process(argv, *, cwd, log_path, progress_path, timeout_seconds=1800,
                idle_timeout_seconds=600, env=None):
    for value in (timeout_seconds, idle_timeout_seconds):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError("Process time budgets must be positive seconds")
    started, updated, heartbeat = time.monotonic(), time.monotonic(), 0.0
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    process, size, reason = None, 0, "running"
    progress = {"version": VERSION, "argv": list(argv), "cwd": str(cwd),
                "log_path": str(log_path), "started_at": time.time(),
                "timeout_seconds": timeout_seconds, "idle_timeout_seconds": idle_timeout_seconds}
    with log_path.open("xb", buffering=0) as log:
        try:
            process = subprocess.Popen(executable_argv(argv), cwd=str(cwd), stdout=log, env=env,
                                       stderr=subprocess.STDOUT, **process_options())
            while True:
                now, current_size = time.monotonic(), os.fstat(log.fileno()).st_size
                if current_size != size:
                    size, updated = current_size, now
                if now - heartbeat >= 1:
                    write_json(progress_path, {**progress, "status": "running", "pid": process.pid,
                        "heartbeat_at": time.time(), "log_bytes": size, "elapsed_seconds": now-started})
                    heartbeat = now
                if process.poll() is not None:
                    reason = "exited"
                    break
                if now - started >= timeout_seconds:
                    reason = "timed_out"
                    break
                if now - updated >= idle_timeout_seconds:
                    reason = "idle_timeout"
                    break
                time.sleep(min(0.1, timeout_seconds / 10, idle_timeout_seconds / 10))
            if reason != "exited":
                _stop_process(process)
            returncode = process.returncode if reason == "exited" else 124
        except BaseException:
            if process is not None:
                _stop_process(process)
            write_json(progress_path, {**progress, "status": "interrupted", "finished_at": time.time(),
                        "pid": process.pid if process is not None else None,
                        "process_started": process is not None,
                        "elapsed_seconds": time.monotonic()-started,
                        "log_bytes": os.fstat(log.fileno()).st_size})
            raise
        finally:
            os.fsync(log.fileno())
    receipt = {**progress, "status": reason, "exit_code": returncode,
               "pid": process.pid, "finished_at": time.time(), "elapsed_seconds": time.monotonic()-started,
               "log_bytes": log_path.stat().st_size, "log_sha256": file_hash(log_path)}
    write_json(progress_path, receipt)
    return receipt


def _lock_byte(stream, shared=False, unlock=False):
    if os.name == "nt":
        import msvcrt
        # _locking supports a range past EOF, so initialization cannot race a writer.
        # Readers conservatively take the same exclusive byte as writers on Windows.
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        mode = fcntl.LOCK_UN if unlock else ((fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        fcntl.flock(stream.fileno(), mode)


@contextmanager
def file_lock(path, *, shared=False, timeout=0):
    """Bounded local lock. False means busy; unexpected OS errors remain failures."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Run lock must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        deadline = time.monotonic() + timeout
        acquired = False
        while True:
            try:
                _lock_byte(stream, shared=shared)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} and getattr(exc, "winerror", None) not in {33, 36}:
                    raise
                if time.monotonic() >= deadline:
                    break
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
        try:
            yield acquired
        finally:
            if acquired:
                _lock_byte(stream, unlock=True)


def deveco_tools_roots(home):
    home = Path(home).expanduser().resolve()
    return [home / "Contents/tools", home / "tools"]


def default_deveco_homes():
    explicit = os.environ.get("DEVECO_HOME")
    homes = [Path(explicit)] if explicit else []
    if os.name == "nt":
        for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(key)
            if base:
                homes.extend([Path(base) / "Huawei/DevEco Studio", Path(base) / "DevEco Studio"])
    else:
        homes.append(Path("/Applications/DevEco-Studio.app"))
    return homes


def deveco_layout(home):
    """Inspect explicit installation layouts; do not run tools during discovery."""
    for tools in deveco_tools_roots(home):
        script = tools / "hvigor/bin/hvigorw.js"
        nodes = [tools / "node/node.exe", tools / "node/bin/node", tools / "node/node"]
        node = next((p for p in nodes if p.is_file()), None)
        if script.is_file() and node is not None:
            return {"home": str(Path(home).expanduser().resolve()), "tools_root": str(tools),
                    "node": str(node), "hvigor": str(script), "build_argv_prefix": [str(node), str(script)],
                    "sdk_home": next((str(p) for p in (Path(home)/"Contents/sdk/default", Path(home)/"sdk/default") if p.is_dir()), None)}
    return None
