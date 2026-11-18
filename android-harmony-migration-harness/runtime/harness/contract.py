"""State coverage, review artifacts and project context for Migration Harness."""
import copy
import math
from pathlib import Path

import foundation as base
from foundation import runtime
from test_plan import compile_points, covered_states, references, visual_bindings
from project import definitions
from execution import validate_execution_policy, policy_references
from requirements import validate as validate_requirements

FORMAT = "migration-harness/1"
PHASES = {"setup", "build", "install", "navigate", "wait", "assert", "capture", "compare", "cleanup"}
AGENT_PHASES = {"discovery", "review_preparation", "review_wait", "implementation", "diagnosis"}
require, text, ids, index = base.require, base.text, base.ids, base.index


def artifact(reference, roots):
    require(isinstance(reference, dict) and reference.get("root") in roots,
            "Artifact needs a declared root")
    require(text(reference.get("locator")), "Artifact needs a source locator")
    path = base.relative_file(Path(roots[reference["root"]]), reference.get("path"))
    return {**base.file_record(path), "locator": reference["locator"]}


def baseline_artifact(reference, roots):
    require(isinstance(reference, dict) and reference.get("root") == "android",
            "Visual baseline must be independent Android evidence")
    record = artifact(reference, roots)
    path = Path(record["path"])
    require(path.suffix.lower() in {".png", ".jpg", ".jpeg"}, "Visual baseline needs a source image")
    with path.open("rb") as stream:
        header = stream.read(8)
    require(header == b"\x89PNG\r\n\x1a\n" or header.startswith(b"\xff\xd8\xff"), "Invalid baseline image")
    base.validate_image(record)
    return record


def material_bundle(reference, roots):
    entry = artifact(reference, roots)
    root = Path(roots[reference["root"]])
    value = reference.get("bundle", str(Path(reference["path"]).parent))
    require(text(value) and not Path(value).is_absolute() and ".." not in Path(value).parts,
            "Review bundle must be relative to its declared root")
    folder = (root / value).resolve(strict=True)
    require(folder.is_dir() and base.inside(folder, root) and base.inside(Path(entry["path"]), folder),
            "Review entry must belong to its resource bundle")
    files = []
    for path in sorted(folder.rglob("*")):
        require(not path.is_symlink(), "Review bundles cannot contain symlinks")
        if path.is_file():
            files.append(base.file_record(path, allow_empty=True))
    return {"entry": entry, "directory": str(folder), "files": files}


def profile_data(contract):
    record = artifact(contract.get("project_profile"), contract["roots"])
    profile = base.read_json(record["path"])
    require(isinstance(profile, dict) and text(profile.get("name")), "Project profile needs a name")
    modules = index(profile.get("modules"), "project modules")
    for module in modules.values():
        require(module.get("root") in contract["roots"] and text(module.get("purpose")),
                "Module needs root and purpose")
        rel = module.get("path")
        require(text(rel) and not Path(rel).is_absolute() and ".." not in Path(rel).parts,
                "Module path must stay in its root")
        root = Path(contract["roots"][module["root"]])
        target = (root / rel).resolve(strict=True)
        require(target.is_dir() and base.inside(target, root), "Module escapes project root")
    knowledge = profile.get("knowledge")
    require(isinstance(knowledge, list) and knowledge, "Declare project knowledge sources")
    records = [artifact(item, contract["roots"]) for item in knowledge]
    tools = index(profile.get("tools"), "project tools")
    for tool in tools.values():
        require(text(tool.get("purpose")) and tool.get("cwd") in contract["roots"],
                "Tool needs purpose and declared cwd")
        argv = tool.get("argv")
        require(isinstance(argv, list) and argv and all(text(v) for v in argv), "Tool needs argv")
    definitions(profile, contract["roots"])
    return profile, [record, *records]


def validate_contract(raw, directory):
    require(isinstance(raw, dict) and raw.get("format") == FORMAT, "Expected Migration Harness contract; use a fresh run")
    raw = copy.deepcopy(raw)
    require(isinstance(raw.get("roots"), dict), "Declare project roots")
    for name, value in raw["roots"].items():
        require(text(value), "Invalid source root")
        raw["roots"][name] = str((Path(directory) / value).resolve(strict=True))
    profile, _ = profile_data(raw)
    project_caps = definitions(profile, raw["roots"])
    for task in raw.get("tasks", []):
        from visual_binding import require_real_binding
        require_real_binding(raw, task)
        if "visual_comparison" in task:
            from visual_binding import adapter_command
            expected_visual = adapter_command()
            require("command" not in task or task["command"] == expected_visual,
                    "Built-in visual comparison owns its adapter command")
            task["command"] = expected_visual
        selected = task.get("project_capability_ids", [])
        require(ids(selected) and set(selected) <= set(project_caps), "Unknown project execution capability")
        if "project_command" in task:
            cid = task["project_command"]
            require(cid in selected and task.get("kind") not in base.AGENT_KINDS,
                    "project_command needs a selected execution capability")
            expected = project_caps[cid]["command"]
            require("command" not in task or task["command"] == expected,
                    "Task command differs from project capability; revise its binding")
            task["command"] = copy.deepcopy(expected)
    planned = "test_plan" in raw
    if planned:
        expected_points = compile_points(raw, raw["test_plan"])
        require(raw.get("acceptance") == expected_points, "Acceptance must be generated from the current test_plan")
        for tid, bindings in visual_bindings(raw["test_plan"]).items():
            task = next(t for t in raw["tasks"] if t["id"] == tid)
            require(task.get("visual_comparison") == bindings, "Visual bindings differ from test_plan; compile the plan again")
    contract = validate_structure(raw, directory)
    if planned:
        for reference in references(contract["test_plan"]):
            artifact(reference, contract["roots"])
    caps = index(contract["capabilities"], "capabilities")
    states = index(contract.get("states"), "states")
    visual_states = {sid for p in contract["acceptance"] if p["kind"] == "visual" for sid in covered_states(p)}
    for state in states.values():
        require(state.get("capability_id") in caps, "State needs a capability")
        require(state.get("kind") in {"page", "dialog", "loading", "error", "permission", "exit", "other"},
                "State needs an explicit kind")
        require(text(state.get("trigger")) and text(state.get("expected")), "State needs trigger and expected behavior")
        sources = state.get("sources")
        require(isinstance(sources, list) and sources, "State needs source evidence")
        for source in sources:
            require(isinstance(source, dict) and source.get("root") == "android", "State behavior needs Android source")
            artifact(source, contract["roots"])
        baselines = state.get("baselines")
        require(isinstance(baselines, list), "State needs a visual baselines list")
        if baselines:
            require("baseline_gap" not in state, "A state cannot have baselines and a baseline_gap")
        elif not planned or state["id"] in visual_states:
            require(text(state.get("baseline_gap")), "Missing visual baselines require a baseline_gap reason")
        for baseline in baselines:
            baseline_artifact(baseline, contract["roots"])
    for cap in caps.values():
        if cap["has_ui"]:
            require(any(s["capability_id"] == cap["id"] for s in states.values()),
                    "Each UI capability needs an explicit state inventory")
    for item in contract["acceptance"]:
        require(planned or "covered_state_ids" not in item, "Grouped states require a test_plan")
        sid = item.get("state_id")
        if item["kind"] in {"device", "visual"} or sid is not None:
            require(sid in states and states[sid]["capability_id"] == item["capability_id"],
                    "Each native acceptance needs its own matching state_id")
    if not planned:
        for sid in states:
            for kind in ("device", "visual"):
                require(any(a.get("state_id") == sid and a["kind"] == kind for a in contract["acceptance"]),
                        "State " + sid + " lacks " + kind + " acceptance")
    scopes = index(contract.get("review_scopes"), "review scopes")
    require(set(scopes) == set(caps), "One joint review scope per capability is required")
    for cid, scope in scopes.items():
        require(ids(scope.get("state_ids")) and set(scope["state_ids"]) ==
                {sid for sid, s in states.items() if s["capability_id"] == cid},
                "Review scope must cover every state of its capability")
        for dimension in ("requirements", "technical", "ui"):
            material_bundle(scope.get(dimension), contract["roots"])
    tasks = index(contract["tasks"], "tasks")
    from visual_binding import validate_binding
    for task in tasks.values():
        validate_binding(task, index(contract["acceptance"], "acceptance"), states, tasks)
    if planned:
        owners = {s["id"] + "-" + v["kind"]: v["task_id"]
                  for s in contract["test_plan"]["scenarios"] for v in s["verifications"]}
        require(all(owners[aid] == t["id"] for t in tasks.values() for aid in t["acceptance_ids"]),
                "Task ownership must match test_plan")
    build = next(t for t in tasks.values() if t["kind"] == "build")
    require({t["id"] for t in tasks.values() if t["kind"] == "implement"} <= base.ancestors(build["id"], tasks),
            "Build must depend on all implementation checkpoints")
    profile, _ = profile_data(contract)
    tool_ids = {t["id"] for t in profile["tools"]}
    for task in tasks.values():
        validate_execution_policy(task)
        validate_requirements(task)
        if task.get("device_suite"):
            from suite import for_task
            require(task["kind"] in {"device", "visual"}, "Device suites are native-test only")
            for_task(base.read_json(artifact(task["device_suite"], contract["roots"])["path"]), task,
                     {point["id"]: point for point in contract["acceptance"]})
        for reference in policy_references(task):
            artifact(reference, contract["roots"])
        for aid, detector in task.get("execution_policy", {}).get("detectors", {}).items():
            if detector.get("baseline"):
                reference = detector["baseline"]["image"]
                require(reference["root"] == "android" and any(
                    baseline["root"] == reference["root"] and baseline["path"] == reference["path"]
                    for sid in covered_states(next(p for p in contract["acceptance"] if p["id"] == aid))
                    for baseline in states[sid]["baselines"]), "Detector baseline must bind this point's declared Android state")
        require(type(task.get("independent_points", False)) is bool,
                "independent_points must be boolean")
        require(not task.get("independent_points") or task["kind"] in base.TEST_KINDS,
                "Only test suites may declare independent points")
        require(ids(task.get("tool_ids", [])) and set(task.get("tool_ids", [])) <= tool_ids,
                "Task references an unknown project tool")
    return contract


def timings(raw, allowed=PHASES):
    require(isinstance(raw, dict) and set(raw) <= allowed, "Unknown timing phase")
    for value in raw.values():
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                "Timing must be finite nonnegative seconds")
    return raw


def validate_structure(raw, directory):
    require(isinstance(raw, dict), "Contract must be an object")
    contract = copy.deepcopy(raw)
    require(contract.get("format") == FORMAT, "Unsupported Harness format")
    require(base.identifier(contract.get("feature_id")), "Invalid feature_id")
    require(contract.get("policy") == "faithful_migration", "Explicit faithful_migration policy required")
    require(type(contract.get("demo", False)) is bool, "demo must be boolean")
    roots = contract.get("roots")
    require(isinstance(roots, dict) and set(roots) == {"android", "harmony", "tools"},
            "Declare android, harmony and tools roots")
    for name, value in roots.items():
        require(text(value), "Invalid source root")
        root = (directory / value).resolve(strict=True)
        require(root.is_dir(), "Source root is not a directory")
        roots[name] = str(root)
    require(roots["android"] != roots["harmony"], "Android and Harmony roots must differ")
    base.input_files(contract.get("production_inputs"), roots)
    require(any(g["root"] == "harmony" for g in contract["production_inputs"]),
            "Production inputs must cover Harmony source and build configuration")
    caps = index(contract.get("capabilities"), "capabilities")
    acceptance = index(contract.get("acceptance"), "acceptance")
    tasks = index(contract.get("tasks"), "tasks")
    for cap in caps.values():
        require(text(cap.get("behavior")), "Capability needs observable behavior")
        require(type(cap.get("has_ui")) is bool, "Capability must declare has_ui")
        require(ids(cap.get("depends_on")) and set(cap["depends_on"]) <= caps.keys()
                and cap["id"] not in cap["depends_on"], "Invalid capability dependency")
        sources = cap.get("android_sources")
        require(isinstance(sources, list) and sources, "Capability needs Android source evidence")
        for source in sources:
            require(isinstance(source, dict) and text(source.get("locator")), "Android source needs a locator")
            base.relative_file(Path(roots["android"]), source["path"])
    base.order_tasks(caps)
    for item in acceptance.values():
        require(item.get("capability_id") in caps and item.get("kind") in base.TEST_KINDS,
                "Acceptance needs a capability and verification kind")
        require(item.get("required") is True and text(item.get("behavior")),
                "Every declared acceptance is required and needs an observable behavior")
        assertions = index(item.get("assertions"), "assertions")
        for assertion in assertions.values():
            require(assertion.get("operator") in {"equal", "truthy"} and "expected" in assertion,
                    "Unsupported or incomplete assertion")
            if assertion["operator"] == "truthy":
                require(assertion["expected"] is True, "truthy requires expected=true")
    require({a["capability_id"] for a in acceptance.values()} == set(caps),
            "Every capability needs acceptance coverage")
    for cid, cap in caps.items():
        if cap["has_ui"] and "test_plan" not in contract:
            for kind in ("visual", "device"):
                require(any(a["capability_id"] == cid and a["kind"] == kind for a in acceptance.values()),
                        "UI capabilities need " + kind + " acceptance")
    assigned = []
    for task in tasks.values():
        require(task.get("kind") in base.KINDS, "Unsupported task kind")
        require(ids(task.get("capability_ids")) and task["capability_ids"]
                and set(task["capability_ids"]) <= caps.keys(), "Invalid task capability mapping")
        require(ids(task.get("depends_on")) and set(task["depends_on"]) <= tasks.keys()
                and task["id"] not in task["depends_on"], "Invalid task dependency")
        require(ids(task.get("acceptance_ids")) and set(task["acceptance_ids"]) <= acceptance.keys(),
                "Invalid task acceptance mapping")
        base.input_files(task.get("inputs"), roots)
        limit = task.get("max_attempts", 5)
        require(type(limit) is int and 1 <= limit <= 20, "max_attempts must be 1..20")
        if task["kind"] in base.TEST_KINDS:
            require(task["acceptance_ids"], "Test task needs acceptance")
            for aid in task["acceptance_ids"]:
                item = acceptance[aid]
                require(item["kind"] == task["kind"] and item["capability_id"] in task["capability_ids"],
                        "Test kind/capability differs from acceptance")
            assigned.extend(task["acceptance_ids"])
        else:
            require(not task["acceptance_ids"], "Only test tasks own acceptance results")
        if task["kind"] not in base.AGENT_KINDS:
            command = task.get("command", {})
            require(isinstance(command, dict), "Command must be an object")
            require(command.get("cwd") in roots, "Command cwd must name a declared root")
            argv = command.get("argv")
            require(isinstance(argv, list) and len(argv) >= 2 and all(text(x) for x in argv)
                    and argv.count("{request}") == 1 and argv.count("{result}") == 1,
                    "argv needs one standalone {request} and {result}")
            require(Path(argv[0]).is_absolute() and Path(argv[0]).is_file(),
                    "Declare the executable's absolute path")
            timeout = command.get("timeout_seconds", 120)
            require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 7200,
                    "Command timeout must be finite, positive and <=7200")
    require(len(assigned) == len(set(assigned)) and set(assigned) == set(acceptance),
            "Every acceptance needs exactly one test owner")
    base.order_tasks(tasks)
    discovers, implementations = {}, {}
    for cid in caps:
        for kind, mapping in (("discover", discovers), ("implement", implementations)):
            matches = [t["id"] for t in tasks.values() if t["kind"] == kind and cid in t["capability_ids"]]
            require(len(matches) == 1, "Each capability needs exactly one " + kind + " task")
            mapping[cid] = matches[0]
    for cid, tid in implementations.items():
        required = {discovers[cid]} | {implementations[d] for d in caps[cid]["depends_on"]}
        required.discard(tid)
        require(required <= base.ancestors(tid, tasks), "Implementation misses discovery/dependency base.ancestors")
    for task in tasks.values():
        prior = base.ancestors(task["id"], tasks)
        if task["kind"] in base.TEST_KINDS:
            require({implementations[c] for c in task["capability_ids"]} <= prior,
                    "Test misses implementation dependency")
        if task["kind"] in {"device", "visual"}:
            require(sum(tasks[t]["kind"] == "build" for t in prior) == 1,
                    "Native test needs exactly one build ancestor")
    require(sum(t["kind"] == "build" for t in tasks.values()) == 1, "Exactly one candidate build task is required")
    require(any(t["kind"] == "device" for t in tasks.values()), "Current-package device smoke is required")
    reviews = [t for t in tasks.values() if t["kind"] == "review"]
    require(len(reviews) == 1 and base.ancestors(reviews[0]["id"], tasks) == set(tasks) - {reviews[0]["id"]},
            "One final review must depend on every task")
    require(set(reviews[0]["capability_ids"]) == set(caps), "Final review must cover all capabilities")
    return contract
