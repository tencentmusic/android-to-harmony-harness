"""A screenshot is usable only after its dedicated browser exits successfully."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from render_prototype import chrome_path, render


class PrototypeRenderTests(unittest.TestCase):
    @unittest.skipUnless(chrome_path(), 'Chromium is required for the actual viewport regression')
    def test_mobile_document_viewport_matches_requested_size(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'viewport.html', root / 'viewport.png'
            source.write_text('<!doctype html><style>html,body{margin:0;background:#ff0000}'
                              '@media(width:390px) and (height:96px){html,body{background:#00ff00}}</style>')
            render(source, output, 390, 96)
            with Image.open(output) as image:
                self.assertEqual(image.size, (390, 96))
                self.assertEqual(image.convert('RGB').getpixel((195, 48)), (0, 255, 0))

    def test_completed_browser_is_reaped_without_signalling_process_group(self):
        self.exercise(0, image=True, succeeds=True)

    def test_failed_browser_cannot_pass_with_a_complete_png(self):
        self.exercise(1, image=True, succeeds=False)

    def test_successful_process_without_image_is_rejected(self):
        self.exercise(0, image=False, succeeds=False)

    def test_timeout_still_stops_owned_browser(self):
        self.exercise(None, image=False, succeeds=False)

    def exercise(self, exit_code, *, image, succeeds):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            html, output = root / 'page.html', root / 'page.png'
            html.write_text('<h1>Audit</h1>')
            process = Mock(returncode=exit_code)
            process.poll.return_value = exit_code
            stop = Mock()

            def launch(*args, **kwargs):
                if image:
                    Image.new('RGB', (64, 64), 'white').save(output)
                return process

            api = {'executable_argv': lambda argv: argv, 'process_options': lambda: {}, '_stop_process': stop}
            with patch('render_prototype.runtime_api', return_value=api), \
                    patch('render_prototype.subprocess.Popen', side_effect=launch), \
                    patch('render_prototype.time.monotonic', side_effect=[0, 46] if exit_code is None else [0, 0]):
                if succeeds:
                    self.assertEqual(render(html, output, 64, 64, browser='chrome')['status'], 'rendered')
                else:
                    with self.assertRaisesRegex(ValueError, 'Prototype rendering'):
                        render(html, output, 64, 64, browser='chrome')
            if exit_code is None:
                stop.assert_called_once_with(process)
            else:
                stop.assert_not_called()
                process.wait.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
