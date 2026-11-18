"""Read-only Harmony project discovery; generated drafts still require real probes."""
import json
import shutil
from pathlib import Path

import foundation as base
from foundation import runtime
from harmony_adapter import test_build_argv


def configuration(path):
    try:
        import json5
    except ImportError:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ValueError("JSON5 discovery requires the json5 package from team requirements.txt") from error
    return json5.loads(path.read_text(encoding="utf-8"), allow_duplicate_keys=False)


def discover(root, project_id, deveco_home=None):
    root = Path(root).resolve(strict=True)
    base.require(base.identifier(project_id), "Discovery needs a stable project_id")
    profile_path = base.relative_file(root, "build-profile.json5")
    config = configuration(profile_path)
    base.require(isinstance(config, dict) and isinstance(config.get("modules"), list), "Build profile needs modules")
    modules, recipes, gaps = [], [], []
    layout = runtime.deveco_layout(deveco_home) if deveco_home else None
    hdc = shutil.which("hdc")
    tools = []
    if layout:
        tools.append({"id": "hvigor", "purpose": "Harmony build", "cwd": "harmony",
                      "argv": [layout["node"], layout["hvigor"]]})
    else:
        gaps.append("Select a DevEco toolchain and verify its invocation")
    if hdc:
        tools.append({"id": "hdc", "purpose": "Harmony device access", "cwd": "harmony", "argv": [hdc]})
    else:
        gaps.append("Configure hdc and a target device")
    for spec in config["modules"]:
        base.require(isinstance(spec, dict), "Module configuration must be an object")
        name, relative = spec.get("name"), spec.get("srcPath")
        base.require(base.text(name) and base.text(relative) and not Path(relative).is_absolute()
                     and ".." not in Path(relative).parts, "Module needs a contained srcPath and name")
        directory = (root / relative).resolve(strict=True)
        base.require(base.inside(directory, root), "Module escapes Harmony root")
        module_path = base.relative_file(root, str(directory.relative_to(root) / "src/main/module.json5"))
        module_config = configuration(module_path)
        base.require(isinstance(module_config, dict) and isinstance(module_config.get("module"), dict), "Module metadata must be an object")
        module = module_config["module"]
        kind = {"entry": "hap", "feature": "hap", "shared": "hsp", "har": "har"}.get(module.get("type"))
        modules.append({"id": name, "root": "harmony", "path": str(directory.relative_to(root)),
                        "purpose": "Discovered module; review ownership", "module_type": kind,
                        "configuration": str(module_path.relative_to(root))})
        if kind in {"hap", "hsp"}:
            prefix = [layout["node"], layout["hvigor"]] if layout else ["CONFIGURE_HVIGOR"]
            recipes.append({"module": name, "type": kind, "test_build_argv": test_build_argv(prefix, name, kind),
                            "status": "draft", "remaining": ["product/target", "signed output paths", "device", "result driver"]})
        elif kind is None:
            gaps.append("Unknown module type: " + name)
    return {"format": "migration-project-discovery/1", "status": "draft", "project_id": project_id,
            "root": str(root), "toolchain": layout,
            "profile": {"name": project_id, "project_id": project_id, "modules": modules,
                "knowledge": [{"root": "harmony", "path": "build-profile.json5", "locator": "Project module configuration"}],
                "tools": tools, "execution_capabilities": []},
            "recipe_templates": recipes, "missing": gaps + ["Review module/root mapping and configure capability probes"],
            "quality_gate_granted": False, "probes_executed": False}
