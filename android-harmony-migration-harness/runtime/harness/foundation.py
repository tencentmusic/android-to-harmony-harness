"""Shared validation, evidence and dependency helpers for Migration Harness."""
import json
import re
import warnings
from pathlib import Path
import runtime

STATE = "harness-state.json"
AGENT_KINDS = {"discover", "implement", "review"}
TEST_KINDS = {"contract", "device", "visual"}
KINDS = AGENT_KINDS | TEST_KINDS | {"build"}


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    def reject(value):
        raise ValueError("Non-finite JSON value: " + value)
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=reject)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def ids(value):
    return (isinstance(value, list) and all(text(x) for x in value)
            and len(value) == len(set(value)))


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value)


def index(rows, label):
    require(isinstance(rows, list) and rows, label + " must not be empty")
    require(all(isinstance(x, dict) and identifier(x.get("id")) for x in rows), "Invalid " + label)
    result = {x["id"]: x for x in rows}
    require(len(result) == len(rows), "Duplicate " + label)
    return result


def inside(path, root):
    return path == root or root in path.parents


def file_record(path, *, allow_empty=False):
    original = Path(path)
    require(not original.is_symlink(), "Evidence cannot be a symlink: " + str(path))
    resolved = original.resolve(strict=True)
    require(resolved.is_file() and (allow_empty or resolved.stat().st_size > 0), "Missing/empty file: " + str(path))
    return {"path": str(resolved), "sha256": runtime.file_hash(resolved)}


def check_record(record):
    require(isinstance(record, dict), "Invalid file record")
    require(file_record(record["path"]) == record, "Changed evidence: " + record["path"])


def validate_image(record):
    from PIL import Image

    expected = {key: record[key] for key in ("path", "sha256")}
    check_record(expected)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(record["path"]) as image:
                require(image.format in {"PNG", "JPEG"} and getattr(image, "n_frames", 1) == 1,
                        "Image evidence must be a single PNG/JPEG frame")
                image.verify()
            with Image.open(record["path"]) as image:
                image.load()
                require(image.width > 0 and image.height > 0, "Image evidence has empty dimensions")
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("Cannot decode image evidence: " + record["path"]) from error
    check_record(expected)


def relative_file(root, value):
    require(text(value) and not Path(value).is_absolute() and ".." not in Path(value).parts,
            "Source paths must be relative and stay in their root")
    path = (root / value).resolve(strict=True)
    require(inside(path, root) and path.is_file(), "Source path escapes root or is not a file")
    return path


def input_files(groups, roots):
    require(isinstance(groups, list) and groups, "Declare non-empty input groups")
    files = set()
    for group in groups:
        require(isinstance(group, dict) and group.get("root") in roots, "Unknown input root")
        root = Path(roots[group["root"]])
        patterns = group.get("include")
        require(ids(patterns) and patterns, "Declare unique input patterns")
        for pattern in patterns:
            require(not Path(pattern).is_absolute() and ".." not in Path(pattern).parts,
                    "Input patterns must stay in their root")
            matches = [p for p in root.glob(pattern) if p.is_file()]
            require(matches, "Input pattern has no files: " + pattern)
            for path in matches:
                require(inside(path.resolve(), root), "Input symlink escapes source root")
                files.add(path.resolve())
    return sorted(files)


def order_tasks(tasks):
    pending, order = set(tasks), []
    while pending:
        ready = sorted(t for t in pending if set(tasks[t]["depends_on"]) <= set(order))
        require(ready, "Task dependency graph has a cycle")
        order.extend(ready)
        pending.difference_update(ready)
    return order


def ancestors(task_id, tasks):
    result, pending = set(), list(tasks[task_id]["depends_on"])
    while pending:
        task = pending.pop()
        if task not in result:
            result.add(task)
            pending.extend(tasks[task]["depends_on"])
    return result
