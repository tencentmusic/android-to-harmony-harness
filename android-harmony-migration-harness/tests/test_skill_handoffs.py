"""Ensure shipped entry instructions can reach their concrete reference files."""
from pathlib import Path
import re
import unittest

PACKAGE = Path(__file__).resolve().parents[1]


class SkillHandoffTests(unittest.TestCase):
    def test_skill_entry_references_exist_in_payload(self):
        for skill in (PACKAGE / 'skills').glob('*/SKILL.md'):
            for reference in re.findall(r'`((?:references/|\.\./[^/]+/references/)[^`]+)`', skill.read_text()):
                if '*' in reference:
                    continue
                with self.subTest(skill=skill.parent.name, reference=reference):
                    self.assertTrue((skill.parent / reference).exists(), reference)

    def test_visual_instructions_reach_packaged_executor_contract(self):
        entry = (PACKAGE / 'skills/android-to-harmony-feature-rollout/SKILL.md').read_text()
        for capability in ('native_screen_capture', 'deterministic_visual_comparison',
                           'real_visual_binding_required', 'plan_visual_bindings'):
            self.assertIn('`' + capability + '`', entry)
        self.assertIn('runtime/harness/VISUAL.md', entry)
        self.assertTrue((PACKAGE / 'runtime/harness/VISUAL.md').is_file())
        self.assertIn('(VISUAL.md)', (PACKAGE / 'runtime/harness/AGENT.md').read_text())


if __name__ == '__main__':
    unittest.main()
