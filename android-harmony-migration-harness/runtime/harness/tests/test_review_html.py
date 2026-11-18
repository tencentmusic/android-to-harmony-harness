import json
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))
from demo import prepare
from engine import Harness
from review_html import write_review_packet


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class ReviewHtmlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.case = Path(self.temp.name) / "case with spaces"
        self.contract = prepare(self.case)

    def init(self):
        return Harness.init(self.contract, self.case / "run")

    def update_contract(self, modify):
        data = json.loads(self.contract.read_text())
        modify(data)
        self.contract.write_text(json.dumps(data))

    def test_review_packet_cli_generates_joint_html(self):
        with tempfile.TemporaryDirectory() as temp:
            case = Path(temp) / "case"
            contract = prepare(case)
            run = case / "run"
            Harness.init(contract, run)
            result = subprocess.run(
                [sys.executable, str(ROOT / "migration.py"), "review-packet", "--run-dir", str(run)],
                capture_output=True, text=True, check=True)
            packet = json.loads(result.stdout)
            self.assertTrue("html_path" in packet, "review-packet must return a human-readable HTML entry")
            html_path = Path(packet["html_path"])
            self.assertTrue(html_path.is_file())
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("<!doctype html>", html.lower())
            for text in ("Synthetic requirements", "Synthetic technical", "Synthetic ui", "photo-source"):
                self.assertIn(text, html)

    def test_generation_preserves_approval_digests_and_state(self):
        e = self.init()
        before = e.review_packet()
        state = (e.root / "harness-state.json").read_bytes()
        packet = write_review_packet(e)
        self.assertEqual(packet['joint_review'], before['joint_review'])
        self.assertEqual(e.review_packet(), before)
        self.assertEqual((e.root / "harness-state.json").read_bytes(), state)
        self.assertEqual(json.loads(Path(packet['packet_path']).read_text()), packet)

    def test_missing_baseline_is_visible_and_not_replaced(self):
        def modify(data):
            data['states'][1]['baselines'] = []
            data['states'][1]['baseline_gap'] = 'No Android device for this dialog'
        self.update_contract(modify)
        packet = write_review_packet(self.init())
        text = Path(packet['html_path']).read_text()
        self.assertIn('No Android device for this dialog', text)
        self.assertIn('photo-source-visual', text)
        self.assertIn('离线合成示例', text)
        self.assertEqual(len(packet['baseline_gaps']), 1)

    def test_missing_material_still_generates_diagnostic_html(self):
        e = self.init()
        (self.case / 'tools/technical.md').unlink()
        packet = write_review_packet(e)
        self.assertEqual(packet['joint_review'][0]['status'], 'stale')
        self.assertIn('材料不可用', Path(packet['html_path']).read_text())

    def test_html_bundle_keeps_assets_and_is_sandboxed(self):
        assets = self.case / 'tools/ui bundle'
        assets.mkdir()
        (assets / 'style.css').write_text('body { color: green }')
        png = (self.case / 'android/baseline.png').read_bytes()
        (assets / 'picture.png').write_bytes(png)
        original = '<link rel="stylesheet" href="style.css"><img src="picture.png"><script>parent.hacked=1</script>'
        (assets / 'index.html').write_text(original)
        self.update_contract(lambda data: data['review_scopes'][0].update(ui={
            'root': 'tools', 'path': 'ui bundle/index.html', 'bundle': 'ui bundle', 'locator': 'UI image'}))
        packet = write_review_packet(self.init())
        entry = Path(packet['html_path'])
        document = Elements(entry.read_text())
        frames = [attrs for tag, attrs in document.tags if tag == 'iframe']
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]['sandbox'], 'allow-same-origin')
        self.assertNotIn('allow-scripts', frames[0]['sandbox'])
        self.assertIn('Content-Security-Policy', frames[0]['srcdoc'])
        self.assertIn('default-src &#x27;none&#x27;', frames[0]['srcdoc'])
        self.assertFalse(any(tag == 'script' for tag, _ in document.tags))
        archive = entry.parent / 'dynamics/files/tools/ui bundle'
        self.assertEqual((archive / 'index.html').read_text(), original)
        self.assertEqual((archive / 'picture.png').read_bytes(), png)
        self.assertTrue((archive / 'style.css').is_file())

    def test_repeat_generation_keeps_old_snapshot_and_reflects_changes(self):
        e = self.init()
        first = write_review_packet(e)
        old_html = Path(first['html_path']).read_bytes()
        (self.case / 'tools/requirements.md').write_text('Changed requirement')
        second = write_review_packet(e)
        self.assertNotEqual(first['html_path'], second['html_path'])
        self.assertNotEqual(first['joint_review'][0]['input_digest'], second['joint_review'][0]['input_digest'])
        self.assertEqual(Path(first['html_path']).read_bytes(), old_html)
        self.assertIn('Changed requirement', Path(second['html_path']).read_text())

    def test_untrusted_text_is_escaped(self):
        payload = '<img src=x onerror="alert(1)">'
        self.update_contract(lambda data: data['capabilities'][0].update(behavior=payload))
        packet = write_review_packet(self.init())
        text = Path(packet['html_path']).read_text()
        self.assertIn('&lt;img', text)
        self.assertFalse(any('onerror' in attrs for _, attrs in Elements(text).tags))

    def test_failed_snapshot_does_not_leave_partial_html(self):
        e = self.init()
        with patch.object(e, 'archive_review', side_effect=ValueError('changed during capture')):
            with self.assertRaisesRegex(ValueError, 'changed during capture'):
                write_review_packet(e)
        self.assertEqual(list((e.root / 'review-packets').iterdir()), [])


if __name__ == "__main__":
    unittest.main()
