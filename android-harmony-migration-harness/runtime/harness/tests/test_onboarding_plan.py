import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import test_project_readiness as projects
import test_engine as fixtures
from engine import Harness, runtime
from onboarding import discover, configuration
from project import verify, readiness, cache_dir
from requirements import compile_plan, policy_gaps


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.case = projects.ProjectReadinessTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_profile_move_reuses_identity_but_new_root_does_not(self):
        profile, path = self.case.configure()
        profile["project_id"] = "stable-project"
        runtime.write_json(path, profile)
        roots = self.case.contract["roots"]
        verify(profile, roots, path, ["build"], True)
        moved = path.with_name("renamed-project.json")
        runtime.write_json(moved, profile)
        self.assertEqual(cache_dir(path, profile), cache_dir(moved, profile))
        self.assertEqual(readiness(profile, roots, moved, True, ["build"])["status"], "verified")
        relocated = self.case.root / "new-harmony"
        __import__("shutil").copytree(roots["harmony"], relocated)
        self.assertEqual(readiness(profile, {**roots, "harmony": str(relocated)}, moved, True, ["build"])["status"], "needs_verification")

    def test_environment_change_is_targeted_and_secrets_are_hashed(self):
        profile, path = self.case.configure()
        profile["execution_capabilities"][0]["environment_keys"] = ["HARNESS_TEST_ENV"]
        runtime.write_json(path, profile)
        roots = self.case.contract["roots"]
        with patch.dict("os.environ", HARNESS_TEST_ENV="secret-value"):
            result = verify(profile, roots, path, ["build", "hsp-test"], True)
        receipt = Path(result["capabilities"][0]["receipt"]["path"]).read_text()
        self.assertNotIn("secret-value", receipt)
        rows = {r["id"]: r for r in readiness(profile, roots, path, True)["capabilities"]}
        self.assertEqual(rows["build"]["status"], "stale")
        self.assertEqual(rows["hsp-test"]["status"], "verified")


class PlanDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ReviewedHarnessTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_missing_preset_policy_blocks_only_its_task(self):
        self.case.contract["tasks"][3]["execution_presets"] = ["artifact-build"]
        e = self.case.ready()
        plan = {r["task_id"]: r for r in compile_plan(e)["tasks"]}
        self.assertEqual(plan["build"]["missing"], ["policy:build_manifest"])
        rows = {r["id"]: r for r in e.status()["tasks"]}
        self.assertEqual(rows["build"]["status"], "input_missing")
        self.assertEqual(rows["contracts"]["status"], "ready")

    def test_point_presets_surface_each_unprotected_point(self):
        task = {"acceptance_ids": ["a", "b"], "execution_presets": ["network-transport"],
                "execution_policy": {"network_exit": {"a": {"url": "https://example.test"}}}}
        self.assertEqual(policy_gaps(task), ["network_exit:b"])

    def test_template_changes_reopen_only_bound_review_and_missing_receipt_cannot_pass(self):
        task = self.case.contract["tasks"][4]
        path = self.case.root / "harmony/suite.json"
        template = {"format": "migration-device-suite/1", "points": [{"acceptance_id": aid,
            "observations": [{"assertion_id": "observed", "condition": {"required_controls": ["content"]}}]}
            for aid in task["acceptance_ids"]]}
        runtime.write_json(path, template)
        task["device_suite"] = {"root": "harmony", "path": "suite.json", "locator": "Reviewed device steps"}
        e = self.case.ready()
        self.assertEqual(e.joint_status("dynamics")["status"], "approved")
        e.run("device-driver-without-binding", task_ids=["device"])
        row = next(r for r in e.status()["tasks"] if r["id"] == "device")
        self.assertEqual(row["status"], "needs_diagnosis")
        self.assertIn("immutable binding", row["reason"])
        template["points"][0]["observations"][0]["condition"] = {"required_controls": ["different-content"]}
        runtime.write_json(path, template)
        self.assertEqual(e.joint_status("dynamics")["status"], "stale")

    def test_hap_hsp_and_windows_style_tool_layout_discovery_never_executes(self):
        root = self.case.root / "harmony"
        runtime.write_json(root / "build-profile.json5", {"modules": [{"name": name, "srcPath": "./" + name}
                           for name in ("entry", "shared")]})
        for name, kind in (("entry", "entry"), ("shared", "shared")):
            runtime.write_json(root / name / "src/main/module.json5", {"module": {"name": name, "type": kind}})
        home = self.case.root / "DevEco tools"
        for relative in ("tools/hvigor/bin/hvigorw.js", "tools/node/node.exe"):
            path = home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Tool placeholder; discovery must not execute it")
        with patch("onboarding.runtime.run_process", side_effect=AssertionError("Discovery executed a tool")):
            draft = discover(root, "project-a", home)
        self.assertFalse(draft["probes_executed"])
        self.assertEqual([m["module_type"] for m in draft["profile"]["modules"]], ["hap", "hsp"])
        self.assertEqual([r["test_build_argv"][2] for r in draft["recipe_templates"]], ["assembleHap", "genOnDeviceTestHap"])
        self.assertFalse(draft["quality_gate_granted"])
        runtime.write_json(root / "build-profile.json5", {"modules": [{"name": "outside", "srcPath": "../tools"}]})
        with self.assertRaisesRegex(ValueError, "contained"):
            discover(root, "project-a")

    @unittest.skipUnless(importlib.util.find_spec("json5"), "JSON5 parser is installed by team requirements.txt")
    def test_real_json5_parser_handles_comments_quotes_and_duplicate_keys(self):
        path = self.case.root / "config.json5"
        path.write_text("{// comment\n modules: [{name:'entry', srcPath:'./entry',},],}")
        self.assertEqual(configuration(path)["modules"][0]["name"], "entry")
        path.write_text("{modules: [], modules: []}")
        with self.assertRaises(ValueError):
            configuration(path)


if __name__ == "__main__":
    unittest.main()
