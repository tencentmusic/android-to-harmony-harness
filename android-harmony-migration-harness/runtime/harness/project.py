"""Project onboarding receipts, independent of migration runs and business sources."""
from pathlib import Path
import os
import shutil
import time
import uuid

import foundation as base
from foundation import runtime

FORMAT = "migration-project-capabilities/1"
require = base.require


def command(value, roots):
    require(isinstance(value, dict) and value.get("cwd") in roots, "Capability command needs a declared cwd")
    require(isinstance(value.get("argv"), list) and value["argv"] and all(base.text(a) for a in value["argv"]),
            "Capability command needs argv")
    timeout = value.get("timeout_seconds", 120)
    require(type(timeout) in (int, float) and 0 < timeout <= 7200, "Invalid capability timeout")


def definitions(profile, roots):
    if "project_id" in profile:
        require(base.identifier(profile["project_id"]), "Project identity must be a stable identifier")
    rows = profile.get("execution_capabilities", [])
    require(isinstance(rows, list), "execution_capabilities must be a list")
    result = base.index(rows, "execution capabilities") if rows else {}
    tools = {t["id"] for t in profile["tools"]}
    for cap in result.values():
        require(base.text(cap.get("version")), "Execution capability needs a version")
        require(base.ids(cap.get("tool_ids")) and cap["tool_ids"] and set(cap["tool_ids"]) <= tools,
                "Execution capability needs known tool_ids")
        require(isinstance(cap.get("inputs"), list) and cap["inputs"],
                "Execution capability needs explicit config/adapter inputs")
        for ref in cap["inputs"]:
            require(isinstance(ref, dict) and ref.get("root") in roots and base.text(ref.get("path")),
                    "Capability input needs root/path")
            require(not Path(ref["path"]).is_absolute() and ".." not in Path(ref["path"]).parts,
                    "Capability input must stay in its root")
        command(cap.get("probe"), roots)
        command(cap.get("command"), roots)
        require(base.ids(cap.get("environment_keys", [])), "Capability environment_keys must be unique identifiers")
    return result


def snapshot(profile, roots, cap, demo=False):
    tools = {t["id"]: t for t in profile["tools"]}
    resolved = []
    substitutions = {"{" + k + "}": v for k, v in roots.items()}
    for tid in cap["tool_ids"]:
        tool = tools[tid]
        executable = substitutions.get(tool["argv"][0], tool["argv"][0])
        # Relative executables are resolved against their declared working root.
        candidate = str(Path(roots[tool["cwd"]]) / executable) if "/" in executable and not Path(executable).is_absolute() else executable
        found = shutil.which(candidate)
        require(found, "Project tool unavailable: " + tid)
        resolved.append({"tool": tool, "executable": base.file_record(Path(found).resolve())})
    executors = []
    for name in ("probe", "command"):
        configured = cap[name]
        cwd = Path(roots[configured["cwd"]])
        program = substitutions.get(configured["argv"][0], configured["argv"][0])
        candidate = str(cwd / program) if "/" in program and not Path(program).is_absolute() else program
        found = shutil.which(candidate)
        require(found, "Capability executor unavailable: " + name)
        files = [base.file_record(Path(found).resolve())]
        for value in configured["argv"][1:]:
            if value.startswith("{") and value.endswith("}"):
                continue
            path = cwd / substitutions.get(value, value)
            if path.is_file():
                files.append(base.file_record(path.resolve()))
        executors.append({"purpose": name, "files": files})
    records = []
    used_roots = {profile_tool["tool"]["cwd"] for profile_tool in resolved}
    used_roots.update(cap[k]["cwd"] for k in ("probe", "command"))
    for ref in cap["inputs"]:
        used_roots.add(ref["root"])
        path = base.relative_file(Path(roots[ref["root"]]), ref["path"])
        records.append(base.file_record(path, allow_empty=True))
    # No globs over production code: only explicitly declared configuration and adapters.
    environment = {key: runtime.digest(os.environ.get(key)) for key in cap.get("environment_keys", [])}
    return {"format": FORMAT, "capability": cap, "tools": resolved, "executors": executors, "inputs": records,
            **({"environment": environment} if environment else {}),
            "roots": {key: roots[key] for key in sorted(used_roots)}, "demo": demo,
            "verifier": base.file_record(Path(__file__))}


def cache_dir(profile_path, profile=None):
    path = Path(profile_path).resolve(strict=True)
    root = Path(os.environ.get("MIGRATION_HARNESS_PROFILE_CACHE", Path.home() / ".cache/migration-harness/project-capabilities"))
    identity = {"project_id": profile["project_id"]} if profile and profile.get("project_id") else str(path)
    return root.resolve() / runtime.digest(identity)


def readiness(profile, roots, profile_path, demo=False, selected=None):
    caps = definitions(profile, roots)
    if selected is not None:
        require(base.ids(selected) and set(selected) <= set(caps), "Unknown execution capability")
        caps = {cid: caps[cid] for cid in selected}
    rows = []
    for cid, cap in caps.items():
        row = {"id": cid, "version": cap["version"], "status": "unverified"}
        try:
            current = snapshot(profile, roots, cap, demo)
            row["input_digest"] = runtime.digest(current)
            pointer = cache_dir(profile_path, profile) / cid / "latest.json"
            if pointer.exists():
                record = base.read_json(pointer)
                base.check_record(record)
                receipt = base.read_json(record["path"])
                require(receipt.get("format") == FORMAT and receipt.get("accepted") is True,
                        receipt.get("reason") or "Project capability probe did not pass")
                require(receipt["input_digest"] == row["input_digest"], "Project configuration/tool/adapter changed")
                require(receipt["snapshot"] == current, "Project capability snapshot changed")
                for evidence in receipt["evidence"]:
                    base.check_record(evidence)
                row.update(status="verified", receipt=record, command=cap["command"],
                           verified_at=receipt["recorded_at"], setup_seconds=receipt["elapsed_seconds"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            row.update(status="stale", reason=str(error))
        rows.append(row)
    return {"status": "not_configured" if not rows else "verified" if all(r["status"] == "verified" for r in rows) else "needs_verification",
            "capabilities": rows, "quality_gate_granted": False,
            "scope": "project_configuration_only", "demo": demo}


def verify(profile, roots, profile_path, selected, demo=False):
    """Explicit onboarding only. Context/status never execute probes or device actions."""
    directory = cache_dir(profile_path, profile)
    require(not any(base.inside(directory.resolve(), Path(root).resolve()) for root in roots.values()),
            "Project capability cache must stay outside source/review roots")
    directory.mkdir(parents=True, exist_ok=True)
    with runtime.file_lock(directory / "verify.lock", timeout=0) as acquired:
        require(acquired, "Project verification already running; inspect its process receipt")
        return _verify(profile, roots, profile_path, selected, demo)


def _verify(profile, roots, profile_path, selected, demo):
    caps = definitions(profile, roots)
    require(base.ids(selected) and selected and set(selected) <= set(caps), "Select known execution capabilities")
    for cid in selected:
        cap = caps[cid]
        inputs = snapshot(profile, roots, cap, demo)
        fingerprint = runtime.digest(inputs)
        pointer = cache_dir(profile_path, profile) / cid / "latest.json"
        if pointer.exists():
            previous_record = base.read_json(pointer)
            base.check_record(previous_record)
            require(not base.read_json(previous_record["path"]).get("running"),
                    "Project probe is unfinished; use project-reconcile before another invocation")
        directory = cache_dir(profile_path, profile) / cid / uuid.uuid4().hex
        directory.mkdir(parents=True)
        request = {"format": FORMAT, "capability_id": cid, "roots": roots, "demo": demo,
                   "input_digest": fingerprint, "capability": cap}
        runtime.write_json(directory / "request.json", request)
        runtime.write_json(directory / "pending-receipt.json", {"format": FORMAT, "capability_id": cid,
            "input_digest": fingerprint, "snapshot": inputs, "accepted": False, "running": True,
            "reason": "Verification in progress; inspect process receipt", "elapsed_seconds": 0,
            "evidence": [base.file_record(directory / "request.json")], "recorded_at": time.time(), "cost_category": "onboarding"})
        runtime.write_json(pointer, base.file_record(directory / "pending-receipt.json"))
        substitutions = {"{" + k + "}": v for k, v in roots.items()}
        substitutions.update({"{request}": str(directory / "request.json"), "{result}": str(directory / "result.json")})
        probe = cap["probe"]
        process = runtime.run_process([substitutions.get(a, a) for a in probe["argv"]],
            cwd=Path(roots[probe["cwd"]]), log_path=directory / "process.log", progress_path=directory / "process.json",
            timeout_seconds=probe.get("timeout_seconds", 120), idle_timeout_seconds=probe.get("timeout_seconds", 120))
        evidence = [base.file_record(directory / name) for name in ("request.json", "process.json")]
        if (directory / "process.log").stat().st_size:
            evidence.append(base.file_record(directory / "process.log"))
        accepted, reason = False, None
        try:
            require(process["status"] == "exited" and process["exit_code"] == 0, "Project capability probe failed")
            raw = base.read_json(directory / "result.json")
            require(raw.get("verified") is True and raw.get("input_digest") == fingerprint,
                    "Probe needs verified=true and current input_digest")
            require(isinstance(raw.get("evidence"), list) and raw["evidence"], "Probe needs validation evidence")
            evidence.extend(base.file_record(p) for p in raw["evidence"])
            evidence.append(base.file_record(directory / "result.json"))
            require(snapshot(profile, roots, cap, demo) == inputs, "Project inputs changed during probe")
            accepted = True
        except (OSError, ValueError, KeyError, TypeError) as error:
            reason = str(error)
        receipt = {"format": FORMAT, "capability_id": cid, "input_digest": fingerprint, "running": False,
                   "snapshot": inputs, "accepted": accepted, "reason": reason, "evidence": evidence,
                   "recorded_at": time.time(), "elapsed_seconds": process["elapsed_seconds"], "cost_category": "onboarding"}
        runtime.write_json(directory / "receipt.json", receipt)
        runtime.write_json(directory.parent / "latest.json", base.file_record(directory / "receipt.json"))
    result = readiness(profile, roots, profile_path, demo)
    result.update(requested_capabilities=selected, verification_succeeded=all(
        r["status"] == "verified" for r in result["capabilities"] if r["id"] in selected))
    return result


def reconcile(profile, roots, profile_path, cid, reason, evidence_paths, action="accept", demo=False):
    """Recover one project probe without blindly repeating baseline/device actions."""
    caps = definitions(profile, roots)
    require(cid in caps and base.text(reason) and action in {"accept", "retry"}, "Reconcile needs capability, action and diagnosis")
    require(isinstance(evidence_paths, list) and evidence_paths, "Reconcile needs diagnostic evidence")
    diagnostic = [base.file_record(p) for p in evidence_paths]
    with runtime.file_lock(cache_dir(profile_path, profile) / "verify.lock", timeout=0) as acquired:
        require(acquired, "Project verification is active")
        pointer = cache_dir(profile_path, profile) / cid / "latest.json"
        record = base.read_json(pointer)
        base.check_record(record)
        receipt = base.read_json(record["path"])
        require(receipt.get("running") is True, "No unfinished project probe")
        for item in receipt["evidence"]:
            base.check_record(item)
        directory = Path(record["path"]).parent
        process = base.read_json(directory / "process.json")
        request = base.read_json(directory / "request.json")
        probe = receipt["snapshot"]["capability"]["probe"]
        substitutions = {"{" + key + "}": value for key, value in request["roots"].items()}
        substitutions.update({"{request}": str(directory / "request.json"), "{result}": str(directory / "result.json")})
        require(request["input_digest"] == receipt["input_digest"] and process.get("argv") ==
                [substitutions.get(a, a) for a in probe["argv"]] and process.get("cwd") == request["roots"][probe["cwd"]]
                and process.get("log_path") == str(directory / "process.log"), "Probe process belongs to another request")
        require(process.get("status") != "running", "Probe is still running or has no terminal record")
        if action == "accept":
            require(process.get("status") == "exited" and process.get("exit_code") == 0, "Probe did not finish successfully")
            require(runtime.digest(snapshot(profile, roots, caps[cid], demo)) == receipt["input_digest"], "Project inputs changed")
            require(process.get("log_sha256") == runtime.file_hash(directory / "process.log"), "Probe log changed")
            raw = base.read_json(directory / "result.json")
            require(raw.get("verified") is True and raw.get("input_digest") == receipt["input_digest"], "Probe result did not verify current inputs")
            require(isinstance(raw.get("evidence"), list) and raw["evidence"], "Probe has no validation evidence")
            receipt["evidence"] += [base.file_record(p) for p in raw["evidence"]] + [base.file_record(directory / "result.json")]
            if (directory / "process.log").stat().st_size:
                receipt["evidence"].append(base.file_record(directory / "process.log"))
            receipt.update(accepted=True, elapsed_seconds=process["elapsed_seconds"])
        else:
            never_started = (process.get("status") == "interrupted" and
                             process.get("process_started") is False and process.get("pid") is None)
            if not never_started:
                require(os.name == "posix" and type(process.get("pid")) is int and process["pid"] > 1,
                        "Retry recovery needs a verifiable POSIX process identity")
                for probe in (os.kill, os.killpg):
                    try:
                        probe(process["pid"], 0)
                    except ProcessLookupError:
                        continue
                    raise ValueError("Probe process or group still exists; cannot retry")
            receipt.update(accepted=False)
        receipt["evidence"] += diagnostic + [base.file_record(directory / "process.json")]
        receipt.update(running=False, reason=reason, reconciled_at=time.time(), recovery_action=action)
        runtime.write_json(directory / "reconciled-receipt.json", receipt)
        runtime.write_json(pointer, base.file_record(directory / "reconciled-receipt.json"))
    return readiness(profile, roots, profile_path, demo, [cid])
