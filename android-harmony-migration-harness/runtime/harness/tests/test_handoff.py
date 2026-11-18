import copy
import json
import subprocess
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

import test_engine as fixtures
from handoff import current, write_handoff
from continuation import advice
from demo import approval_demo, record_demo


def example():
    return json.loads((fixtures.ROOT / "examples/developer_handoff.json").read_text())


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class DeveloperHandoffTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ReviewedHarnessTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.e = self.case.execute_suite()
        self.value = example()

    def test_project_output_fixed_order_and_internal_evidence(self):
        before = (self.e.root / "harness-state.json").read_bytes()
        fingerprints = {tid: self.e.fingerprint(task) for tid, task in self.e.tasks.items()}
        result = write_handoff(self.e, self.value)
        target = Path(result["html_path"])
        self.assertEqual(target.parent, (self.case.root / "harmony/androidtoharmonyharnessdoc").resolve())
        self.assertEqual(Path(result["packet_path"]).parent, self.e.root / "developer-handoffs")
        doc = target.read_text()
        parser = Elements(doc)
        ids = [attrs["id"] for tag, attrs in parser.tags if tag == "section" and "id" in attrs]
        self.assertEqual(ids, ["overview", "pending", "requirements", "technical", "ui", "code", "verification", "integration"])
        for value in ("input_digest", "sha256", "run_id", "回灌", "下载候选包"):
            self.assertNotIn(value, doc)
        self.assertFalse(any(tag in {"script", "iframe"} for tag, _ in parser.tags))
        self.assertEqual((self.e.root / "harness-state.json").read_bytes(), before)
        self.assertEqual(fingerprints, {tid: self.e.fingerprint(task) for tid, task in self.e.tasks.items()})

    def test_explicit_nested_deveco_project_and_path_escape_rejected(self):
        project = self.case.root / "harmony/apps/live"
        project.mkdir(parents=True)
        value = {**self.value, "project": {"root": "harmony", "path": "apps/live"}}
        self.assertEqual(Path(write_handoff(self.e, value)["html_path"]).parent, (project / "androidtoharmonyharnessdoc").resolve())
        for project in ({"root": "tools", "path": "."}, {"root": "harmony", "path": "../tools"},
                        {"root": "harmony", "path": str(self.case.root / "tools")}):
            with self.subTest(project=project), self.assertRaises(ValueError):
                write_handoff(self.e, {**self.value, "project": project})

    def test_symlink_output_directory_rejected(self):
        (self.case.root / "harmony/androidtoharmonyharnessdoc").symlink_to(self.case.root / "tools", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "real directory"):
            write_handoff(self.e, self.value)

    def test_backend_has_no_automatic_visual_collection(self):
        result = write_handoff(self.e, self.value)
        self.assertFalse(any(tag == "img" for tag, _ in Elements(Path(result["html_path"]).read_text()).tags))
        self.assertIn(self.value["ui"]["reason"], Path(result["html_path"]).read_text())

    def test_selected_ui_images_embedded_and_changed_image_stales_document(self):
        value = copy.deepcopy(self.value)
        image = self.case.root / "android/baseline.png"
        value["ui"] = {"applicable": True, "summary": "设计效果", "images": [
            {"root": "android", "path": "baseline.png", "locator": "图片来源弹窗 · Android 对照"}]}
        result = write_handoff(self.e, value)
        tags = Elements(Path(result["html_path"]).read_text()).tags
        self.assertTrue(next(attrs["src"] for tag, attrs in tags if tag == "img").startswith("data:image/png;base64,"))
        image.write_bytes(image.read_bytes() + b"changed")
        self.assertEqual(current(self.e)["status"], "stale")

    def test_knowledge_must_be_completed_or_explained_and_remains_internal(self):
        for knowledge in ({"status": "pending", "summary": "稍后处理"},
                          {"status": "completed", "summary": "已回灌", "evidence": []},
                          {"status": "not_applicable", "summary": ""}):
            with self.subTest(knowledge=knowledge), self.assertRaises(ValueError):
                write_handoff(self.e, {**self.value, "knowledge": knowledge})
        note = self.case.root / "tools/backfill.txt"
        note.write_text("Recorded project lesson and index update")
        value = {**self.value, "knowledge": {"status": "completed", "summary": "知识已更新", "evidence": [
            {"root": "tools", "path": "backfill.txt", "locator": "实际写入记录"}]}}
        result = write_handoff(self.e, value)
        self.assertNotIn("backfill.txt", Path(result["html_path"]).read_text())
        note.write_text("changed")
        self.assertEqual(current(self.e)["status"], "stale")

    def test_missing_point_results_cannot_be_hidden_by_empty_pending(self):
        settings = self.case.root / "tools/settings.json"
        settings.write_text('{"omit_popup":true}')
        value = {**self.value, "pending": []}
        result = write_handoff(self.e, value)
        doc = Path(result["html_path"]).read_text()
        pending = doc.split('<section id="pending">')[1].split('</section>')[0]
        self.assertIn("尚未通过的合同检查", pending)
        self.assertIn("受阻", pending)
        self.assertFalse(advice(self.e)["can_end_turn"])

    def test_report_alone_is_not_a_handoff_and_final_approval_is_preserved(self):
        from report import write_report
        record_demo(self.e, "review", "review")
        write_report(self.e)
        self.assertEqual(advice(self.e)["next_action"]["action"], "prepare_developer_handoff")
        write_handoff(self.e, self.value)
        self.assertEqual(advice(self.e)["next_action"]["action"], "final_review")
        approval_demo(self.e, "final", "final")
        self.assertEqual(current(self.e)["status"], "current")
        self.assertEqual(advice(self.e)["status"], "demo_complete")
        self.assertEqual(self.e.verify()["status"], "demo_only")

    def test_source_document_or_receipt_changes_require_new_handoff(self):
        record_demo(self.e, "review", "review")
        first = write_handoff(self.e, self.value)
        old = Path(first["html_path"]).read_bytes()
        changed = write_handoff(self.e, {**self.value, "title": "第二版交接"})
        self.assertNotEqual(first["html_path"], changed["html_path"])
        self.assertEqual(Path(first["html_path"]).read_bytes(), old)
        Path(changed["html_path"]).write_text("tampered")
        self.assertEqual(current(self.e)["status"], "stale")
        self.assertFalse(advice(self.e)["can_end_turn"])

    def test_html_escapes_authored_text(self):
        payload = '<img src=x onerror="alert(1)">'
        result = write_handoff(self.e, {**self.value, "title": payload})
        doc = Path(result["html_path"]).read_text()
        self.assertIn("&lt;img", doc)
        self.assertFalse(any("onerror" in attrs for _, attrs in Elements(doc).tags))

    def test_export_overlapping_inputs_rolls_back_without_invalidating_build(self):
        self.e.contract["production_inputs"].append({"root": "harmony", "include": ["**/*"]})
        before = self.e.fingerprint(self.e.tasks["build"])
        with self.assertRaisesRegex(ValueError, "overlaps source inputs"):
            write_handoff(self.e, self.value)
        self.assertFalse((self.e.root / "developer-handoff.json").exists())
        self.assertEqual(list((self.case.root / "harmony/androidtoharmonyharnessdoc").iterdir()), [])
        self.assertEqual(self.e.fingerprint(self.e.tasks["build"]), before)

    def test_cli_exposes_handoff_and_capabilities_without_source_changes(self):
        source = self.case.root / "handoff-input.json"
        source.write_text(json.dumps(self.value))
        result = subprocess.run([sys.executable, str(fixtures.ROOT / "migration.py"), "handoff", "--run-dir", str(self.e.root),
                                 "--input", str(source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(result.stdout)
        self.assertTrue(Path(packet["html_path"]).is_file())
        self.assertEqual(packet["developer_handoff"]["status"], "current")
        capabilities = subprocess.run([sys.executable, str(fixtures.ROOT / "migration.py"), "capabilities"],
                                      capture_output=True, text=True, check=True)
        self.assertTrue(json.loads(capabilities.stdout)["developer_handoff_html"])


if __name__ == "__main__":
    unittest.main()
