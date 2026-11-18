"""Recipe adapter for verified Harmony build and test lifecycles.

Usage: python harmony_adapter.py REQUEST RESULT RECIPE
Project recipes own exact commands/paths. No task discovery, glob or mtime selection.
"""
import shutil
import sys
import time
from pathlib import Path

import foundation as base
from foundation import runtime
from execution import ExecutionFailure, package_manifest, failure_record

PHASES = {"setup", "build", "install", "navigate", "wait", "assert", "capture", "compare", "cleanup"}


def test_build_argv(hvigor, module, module_type, product="default", target="default"):
    base.require(module_type in {"hap", "hsp"} and base.text(module), "Declare HAP/HSP module type")
    prefix = [hvigor] if isinstance(hvigor, str) else hvigor
    base.require(isinstance(prefix, list) and prefix and all(base.text(arg) for arg in prefix),
                 "Declare a non-empty Hvigor argv prefix")
    task = "genOnDeviceTestHap" if module_type == "hsp" else "assembleHap"
    return [*prefix, task, "--mode", "module", "-p", "module=" + module + "@ohosTest",
            "-p", "product=" + product, "-p", "target=" + target]


def resolve_output(directory, relative):
    base.require(base.text(relative) and not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                 "Adapter outputs must be relative to the current attempt")
    target = directory / relative
    base.require(base.inside(target.resolve(), directory.resolve()), "Adapter output escapes attempt")
    return target


def run_recipe(request_path, result_path, recipe_path):
    request_path, result_path, recipe_path = map(lambda p: Path(p).resolve(), (request_path, result_path, recipe_path))
    request, recipe = base.read_json(request_path), base.read_json(recipe_path)
    directory = result_path.parent
    roots = request["roots"]
    substitutions = {"{" + k + "}": v for k, v in roots.items()}
    substitutions.update({"{output}": str(directory), "{request}": str(request_path), "{result}": str(result_path)})
    steps = recipe.get("steps")
    base.require(isinstance(steps, list) and steps, "Recipe needs lifecycle steps")
    if recipe.get("test_module"):
        module = recipe["test_module"]
        steps = [{"id": "build-device-test", "phase": "build", "command": {
            "cwd": module["root"], "cwd_path": module.get("cwd_path", "."),
            "argv": test_build_argv(module.get("build_argv_prefix", module.get("hvigor")), module["name"],
                module["type"], module.get("product", "default"), module.get("target", "default")),
            "timeout_seconds": module.get("timeout_seconds", 120)}}] + steps
    base.require(base.ids([s.get("id") for s in steps]), "Recipe step IDs must be unique")
    phases, receipts, evidence = {}, [], [str(recipe_path)]
    failure = None
    try:
        for step in steps:
            # Cleanup still runs after a failed lifecycle step.
            if failure and step.get("phase") != "cleanup":
                continue
            phase = step.get("phase")
            base.require(phase in PHASES, "Unknown recipe phase")
            command = step.get("command", {})
            root = Path(roots[command["cwd"]]).resolve(strict=True)
            relative = command.get("cwd_path", ".")
            base.require(not Path(relative).is_absolute() and ".." not in Path(relative).parts, "Invalid recipe cwd_path")
            cwd = (root / relative).resolve(strict=True)
            base.require(base.inside(cwd, root), "Recipe cwd escapes root")
            argv = command.get("argv")
            base.require(isinstance(argv, list) and argv and all(base.text(a) for a in argv), "Recipe step needs argv")
            stem = "step-" + str(len(receipts))
            started = time.monotonic()
            try:
                process = runtime.run_process([substitutions.get(a, a) for a in argv], cwd=cwd,
                    log_path=directory / (stem + ".log"), progress_path=directory / (stem + ".json"),
                    timeout_seconds=command.get("timeout_seconds", 120), idle_timeout_seconds=command.get("timeout_seconds", 120))
            except (OSError, ValueError) as error:
                phases[phase] = phases.get(phase, 0) + time.monotonic() - started
                receipts.append({"id": step["id"], "phase": phase, "error": str(error)})
                for suffix in (".json", ".log"):
                    path = directory / (stem + suffix)
                    if path.is_file() and path.stat().st_size:
                        evidence.append(str(path))
                failure = failure or failure_record({"category": "tool_failure", "code": "lifecycle_launch",
                    "step": step["id"], "reason": str(error), "recovery_point": step["id"]})
                continue
            phases[phase] = phases.get(phase, 0) + process["elapsed_seconds"]
            receipts.append({"id": step["id"], "phase": phase, "process": process})
            evidence.append(str(directory / (stem + ".json")))
            if (directory / (stem + ".log")).stat().st_size:
                evidence.append(str(directory / (stem + ".log")))
            if process["status"] != "exited" or process["exit_code"] != 0:
                failure = failure or failure_record({"category": "tool_failure", "code": "lifecycle_command",
                    "step": step["id"], "reason": "Lifecycle command failed; inspect its process receipt",
                    "recovery_point": step["id"]})
        if failure:
            raise ExecutionFailure(**{k: failure[k] for k in ("category", "code", "step", "reason", "recovery_point")})
        started = time.monotonic()
        if recipe.get("modules"):
            modules = []
            for index, spec in enumerate(recipe["modules"]):
                source = base.relative_file(Path(roots[spec["root"]]), spec["path"])
                target = directory / (str(index) + "-" + source.name)
                shutil.copyfile(source, target)
                proof = resolve_output(directory, spec["signature_evidence"])
                modules.append({"archive_name": spec["archive_name"], "path": str(target), "signature_evidence": str(proof)})
                evidence += [str(target), str(proof)]
            manifest = package_manifest(directory / "candidate.app", modules)
            raw = {"package_path": manifest["package"]["path"], "output_manifest": manifest,
                   "evidence": evidence + [manifest["package"]["path"]]}
        else:
            # A project test driver exports the contract's point schema, keeping
            # Hypium/service specifics out of the shared adapter.
            normalized = resolve_output(directory, recipe["normalized_result"])
            raw = base.read_json(normalized)
            base.require(normalized != result_path, "Normalized result must differ from the adapter result")
            evidence.append(str(normalized))
            if request.get("format") == "migration-project-capabilities/1":
                base.require(raw.get("verified") is True, "Onboarding lifecycle assertions did not pass")
            else:
                base.require(isinstance(raw.get("points"), list), "Test driver must export point-level results")
        phases["setup"] = phases.get("setup", 0) + time.monotonic() - started
        if request.get("format") == "migration-project-capabilities/1":
            raw = {"verified": True, "input_digest": request["input_digest"], "evidence": evidence + raw.get("evidence", [])}
        else:
            for point in raw.get("points", []):
                point["evidence"] = list(dict.fromkeys(point.get("evidence", []) + evidence))
        raw.update(phase_seconds=phases, lifecycle_steps=receipts)
    except ExecutionFailure as error:
        failure = failure_record(error.failure)
        raw = {"verified": False, "input_digest": request["input_digest"], "failure": failure,
               "points": [{"acceptance_id": aid, "status": "blocked", "reason": str(error),
                           "failure": failure, "evidence": evidence}
                          for aid in request.get("requested_acceptance_ids", [])], "phase_seconds": phases,
               "lifecycle_steps": receipts}
        # Build adapters must fail the process; a blocked point is a test result only.
        runtime.write_json(result_path, raw)
        if request.get("task", {}).get("kind") == "build":
            raise
    runtime.write_json(result_path, raw)
    return raw


if __name__ == "__main__":
    run_recipe(*sys.argv[1:])
