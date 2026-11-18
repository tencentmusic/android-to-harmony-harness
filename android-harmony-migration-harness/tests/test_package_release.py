"""Exercise the installed and exported payload without the source checkout."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))

from common import SKILLS, integrity, read, sha
from export_release import export_release
from install import install, registration_matches
from runtime_discovery import discover
from doctor import diagnose
from unittest.mock import patch


class PackageReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='harness-package-release-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name) / 'portable space & 中文'
        cls.work.mkdir()
        cls.installed = cls.work / 'team-package'
        cls.skills = cls.work / 'initial-skills'
        cls.receipt = install(source=PACKAGE, install_dir=cls.installed, skills_dir=cls.skills,
                              skip_deps=True, registration='pointer')

    def cli(self, root, script, *args):
        environment = {**os.environ, 'PYTHONPATH': '', 'PYTHONDONTWRITEBYTECODE': '1'}
        return subprocess.run([sys.executable, str(root / script), *map(str, args)],
                              cwd=self.work, env=environment, capture_output=True,
                              encoding='utf-8', timeout=60)

    def test_release_contains_one_maintained_runtime_without_old_engines(self):
        release = read(PACKAGE / 'release-files.json')
        self.assertEqual({p.name for p in (PACKAGE / 'runtime').iterdir() if p.is_dir()}, {'harness'})
        for path in (PACKAGE / 'runtime/harness').rglob('*.py'):
            self.assertEqual(release[path.relative_to(PACKAGE).as_posix()], sha(path))
        self.assertFalse((PACKAGE.parent / 'experiments').exists())
        for obsolete in ('scripts/harness_v2.py', 'scripts/unified_cli.py', 'runtime-sources.json',
                         'skills/android-to-harmony-feature-rollout/scripts/migration_harness.py',
                         'skills/android-to-harmony-feature-rollout/LEGACY-WORKFLOW.md'):
            self.assertNotIn(obsolete, release)
            self.assertFalse((PACKAGE / obsolete).exists())

    def test_installed_cli_and_skill_registration_are_self_contained(self):
        self.assertFalse((self.work / 'experiments').exists())
        self.assertEqual(self.receipt['skills'], list(SKILLS))
        self.assertNotIn('migration-harness-v2', self.receipt['skills'])
        self.assertEqual(len(SKILLS), 3)
        self.assertFalse((self.installed / 'skills/migration-harness-v2/SKILL.md').exists())
        self.assertEqual(self.receipt['version'], read(PACKAGE / '.codex-plugin/plugin.json')['version'])
        self.assertEqual(integrity(self.installed), [])
        for name in SKILLS:
            self.assertTrue(registration_matches(self.skills / name, self.installed / 'skills' / name))
        self.assertFalse((self.installed / 'scripts/harness_v2.py').exists())
        self.assertFalse((self.installed / 'release-v2-capabilities.json').exists())
        result = self.cli(self.installed, 'scripts/harness.py', 'capabilities')
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['format'], 'migration-harness/1')
        self.assertEqual(value['default_workflow'], 'joint_review_state_acceptance')
        self.assertTrue(value['managed_agent_runner'])
        self.assertFalse(value['opt_in'])
        self.assertIn('start', value['commands'])
        self.assertNotIn('compatibility', value)
        self.assertEqual(value, read(self.installed / 'release-capabilities.json'))

    def test_installed_selftest_uses_unified_entry_and_rejects_demo_delivery(self):
        result = self.cli(self.installed, 'scripts/selftest.py')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'passed', report)
        self.assertFalse(report['quality_gate_granted'])
        self.assertIn('demo_never_grants_real_delivery', {c['check'] for c in report['checks']})
        self.assertTrue({'visual_pixels_positive_and_negative', 'visual_bound_lifecycle'} <=
                        {c['check'] for c in report['checks']})

    def test_installed_demo_reports_omitted_point_and_verify_fails(self):
        output = self.work / 'omitted-demo'
        result = self.cli(self.installed, 'runtime/harness/examples/demo.py',
                          '--output', output, '--omit-popup')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('not_checked', result.stdout)
        result = self.cli(self.installed, 'scripts/harness.py', 'verify', '--run-dir', output / 'run')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotEqual(json.loads(result.stdout)['status'], 'delivery_ready')

    def test_pointer_registration_is_idempotent(self):
        before = {p.relative_to(self.skills): p.read_bytes() for p in self.skills.rglob('*') if p.is_file()}
        install(source=PACKAGE, install_dir=self.installed, skills_dir=self.skills,
                skip_deps=True, registration='pointer')
        after = {p.relative_to(self.skills): p.read_bytes() for p in self.skills.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_project_router_uses_the_only_packaged_process_runtime(self):
        config = self.work / 'routing.json'
        target = self.work / 'routed-harmony'
        target.mkdir()
        config.write_text(json.dumps({'projects': [{
            'id': 'portable-project', 'target_roots': [str(target)],
            'source_roots': [str(self.work)], 'profile': 'profiles/interactive-demo.md',
            'build_cwd': str(target), 'analysis_output_dir': str(target / 'analysis'),
            'deveco_home': str(self.work / 'missing-deveco')}]}), encoding='utf-8')
        result = self.cli(self.installed, 'skills/android-to-harmony/scripts/route_project.py',
                          '--path', target, '--config', config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['project'], 'portable-project')
        self.assertIsNone(json.loads(result.stdout)['toolchain'])

    def test_public_start_defaults_to_joint_review_and_resume_is_read_only(self):
        demo = self.work / 'default-start-demo'
        result = self.cli(self.installed, 'runtime/harness/examples/demo.py', '--output', demo)
        self.assertEqual(result.returncode, 0, result.stderr)
        new_run = self.work / 'new-default-run'
        result = self.cli(self.installed, 'scripts/harness.py', 'start', '--contract', demo / 'contract.json', '--run-dir', new_run)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = read(new_run / 'harness-state.json')
        self.assertEqual(state['format'], 'migration-harness/1')
        self.assertEqual(state['joint_reviews'], {})
        self.assertEqual(state['tasks'], {})
        before = {p.relative_to(demo / 'run'): p.read_bytes() for p in (demo / 'run').rglob('*.json')}
        result = self.cli(self.installed, 'scripts/harness.py', 'resume', '--run-dir', demo / 'run', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, {p.relative_to(demo / 'run'): p.read_bytes() for p in (demo / 'run').rglob('*.json')})
        result = self.cli(self.installed, 'scripts/harness.py', 'verify', '--run-dir', demo / 'run')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'demo_only')

    def test_removed_formats_are_rejected_without_modifying_original_records(self):
        for number in (1, 2):
            run = self.work / ('removed-format-' + str(number))
            run.mkdir()
            state = run / 'harness-state.json'
            state.write_text(json.dumps({'format': 'autonomous-migration-experiment/' + str(number)}))
            before = state.read_bytes()
            result = self.cli(self.installed, 'scripts/harness.py', 'resume', '--run-dir', run)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('Unsupported run format', result.stdout + result.stderr)
            self.assertEqual(before, state.read_bytes())

    def test_retired_registrations_in_both_roots_are_removed_and_transaction_rolls_back(self):
        modern, legacy = self.work / 'retired-modern', self.work / 'retired-legacy'
        for root in (modern, legacy):
            old = root / 'migration-harness-v2'
            old.mkdir(parents=True)
            (old / 'SKILL.md').write_text('preserve custom content', encoding='utf-8')
        with patch('install.create_registration', side_effect=OSError('registration failure')):
            with self.assertRaisesRegex(OSError, 'registration failure'):
                install(source=PACKAGE, install_dir=self.installed, skills_dir=modern, legacy_skills_dir=legacy,
                        replace=True, skip_deps=True, registration='pointer')
        for root in (modern, legacy):
            self.assertEqual((root / 'migration-harness-v2/SKILL.md').read_text(), 'preserve custom content')
        result = install(source=PACKAGE, install_dir=self.installed, skills_dir=modern, legacy_skills_dir=legacy,
                         replace=True, skip_deps=True, registration='pointer')
        for root in (modern, legacy):
            self.assertFalse((root / 'migration-harness-v2').exists())
        for kind in ('modern', 'legacy'):
            self.assertEqual((Path(result['backups']) / kind / 'migration-harness-v2/SKILL.md').read_text(), 'preserve custom content')

    def test_runtime_discovery_requires_explicit_host_selection_and_checks_capabilities(self):
        legacy = self.work / 'legacy-skills/migration-harness-v2'
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / 'SKILL.md').write_text('Legacy unmanaged skill', encoding='utf-8')
        found = discover(skills_dirs=[self.skills, legacy.parent])
        self.assertEqual(found['selection_status'], 'host_selection_unknown')
        self.assertEqual(len(found['candidates']), 2)
        selected = discover(skills_dirs=[self.skills], selected_skill=self.skills / 'android-to-harmony-feature-rollout/SKILL.md',
                            required_capabilities=['end_check_receipts', 'declarative_device_suites'])
        self.assertEqual(selected['selection']['status'], 'verified_package_runtime')
        self.assertTrue(selected['selection']['capabilities']['read_only_recovery'])
        self.assertEqual(selected['selection']['runtime'], str((self.installed / 'scripts/harness.py').resolve()))
        unavailable = discover(skills_dirs=[], launcher=self.installed / 'scripts/harness.py', required_capabilities=['not_implemented'])
        self.assertEqual(unavailable['selection']['status'], 'missing_capabilities')

    def test_runtime_discovery_detects_selected_runtime_tampering_without_running_it(self):
        source = self.installed / 'runtime/harness/session.py'
        original = source.read_bytes()
        source.write_bytes(original + b'\nraise RuntimeError("Must never execute discovery targets")\n')
        try:
            found = discover(skills_dirs=[], launcher=self.installed / 'scripts/harness.py')
            self.assertEqual(found['selection']['status'], 'different')
            self.assertIn('runtime/harness/session.py', found['selection']['changed_files'])
        finally:
            source.write_bytes(original)

    def test_doctor_requires_an_explicit_runtime_for_required_capabilities(self):
        with patch('doctor.importlib.util.find_spec', return_value=True):
            for capability in ('end_check_receipts', 'not_implemented'):
                with self.subTest(capability=capability):
                    result = diagnose(root=self.installed, required_capabilities=[capability],
                                      path_launcher=self.work / 'missing-launcher')
                    self.assertEqual(result['status'], 'needs_setup')
                    self.assertIn('runtime_selection_required', result['issues'])
            result = diagnose(root=self.installed, launcher=self.installed / 'scripts/harness.py',
                              required_capabilities=['end_check_receipts'],
                              path_launcher=self.work / 'missing-launcher')
            self.assertEqual(result['status'], 'core_ready', result['issues'])
            result = diagnose(root=self.installed, launcher=self.installed / 'scripts/harness.py',
                              required_capabilities=['not_implemented'],
                              path_launcher=self.work / 'missing-launcher')
            self.assertEqual(result['status'], 'needs_setup')
            self.assertIn('selected_runtime_not_verified:missing_capabilities', result['issues'])

    def test_discovery_does_not_verify_an_unlisted_retired_skill_in_a_valid_package(self):
        retired = self.installed / 'skills/migration-harness-v2'
        retired.mkdir()
        try:
            (retired / 'SKILL.md').write_text('Do not run the unified workflow', encoding='utf-8')
            selected = discover(skills_dirs=[], selected_skill=retired / 'SKILL.md')['selection']
            self.assertEqual(selected['status'], 'different')
            self.assertIsNone(selected['capabilities'])
        finally:
            (retired / 'SKILL.md').unlink()
            retired.rmdir()

    def test_installed_session_and_end_check_preserve_demo_quality_boundary(self):
        demo = self.work / 'lifecycle-demo'
        result = self.cli(self.installed, 'runtime/harness/examples/demo.py', '--output', demo)
        self.assertEqual(result.returncode, 0, result.stderr)
        start = self.work / 'session-start.json'
        start.write_text(json.dumps({'action':'start','session_id':'host-1','request':'Run full migration','scope':{'mode':'full'}}))
        result = self.cli(self.installed, 'scripts/harness.py', 'session', '--run-dir', demo / 'run',
                          '--operation-id', 'start', '--input', start)
        self.assertEqual(result.returncode, 0, result.stderr)
        stop = self.work / 'session-stop.json'
        stop.write_text(json.dumps({'session_id':'host-1','reason':'complete'}))
        result = self.cli(self.installed, 'scripts/harness.py', 'end-check', '--run-dir', demo / 'run',
                          '--operation-id', 'end', '--input', stop)
        # The demo has no developer handoff, so even its successful checks do not permit stopping.
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(json.loads(result.stdout)['quality_gate_granted'])
        self.assertFalse(json.loads(result.stdout)['automatic_host_hook'])

    def test_installed_managed_agent_protocol_runs_without_source_checkout(self):
        output = self.work / 'managed-agent-demo'
        result = self.cli(self.installed, 'runtime/harness/examples/managed_agent_demo.py', '--output', output)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['turns'], 2)
        self.assertTrue(receipt['can_end_turn'])
        self.assertFalse(receipt['quality_gate_granted'])
        requests = list((output / 'run/agent-turns').glob('*/request.json'))
        self.assertEqual(len(requests), 2)
        for request in requests:
            self.assertEqual(read(request)['harness_command'][1], str((self.installed / 'scripts/harness.py').resolve()))
        result = self.cli(self.installed, 'scripts/harness.py', 'agent-check', '--adapter', output / 'agent.json', '--require-enforcement')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['active_enforcement'])
        result = self.cli(self.installed, 'scripts/doctor.py', '--agent-adapter', output / 'agent.json', '--require-agent-enforcement')
        # skip_deps installation can lack visual/json5 packages; integration is still inspected independently.
        diagnosis = json.loads(result.stdout)
        self.assertEqual(diagnosis['agent_integration']['status'], 'ready')
        self.assertTrue(diagnosis['agent_integration']['enforcement_available'])
        hook = self.work / 'external-hook.json'
        hook.write_text(json.dumps({'format':'harness-agent-adapter/1','id':'external','mode':'host_hook'}))
        result = self.cli(self.installed, 'scripts/doctor.py', '--agent-adapter', hook, '--require-agent-enforcement')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('agent_enforcement_unavailable', json.loads(result.stdout)['issues'])

    def test_retired_skill_is_preserved_then_backed_up_without_reregistration(self):
        skills = self.work / 'conflicting-skills'
        old = skills / 'migration-harness-v2' / 'SKILL.md'
        old.parent.mkdir(parents=True)
        old.write_text('personal v2 skill', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Existing skills or launcher preserved'):
            install(source=PACKAGE, install_dir=self.installed, skills_dir=skills,
                    skip_deps=True, registration='pointer')
        self.assertEqual(list(skills.iterdir()), [old.parent])
        self.assertEqual(old.read_text(encoding='utf-8'), 'personal v2 skill')
        result = install(source=PACKAGE, install_dir=self.installed, skills_dir=skills,
                         replace=True, skip_deps=True, registration='pointer')
        backup = Path(result['backups']) / 'modern/migration-harness-v2/SKILL.md'
        self.assertEqual(backup.read_text(encoding='utf-8'), 'personal v2 skill')
        self.assertFalse(old.parent.exists())
        self.assertTrue(registration_matches(skills / 'android-to-harmony-feature-rollout',
                                             self.installed / 'skills/android-to-harmony-feature-rollout'))

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink registration')
    def test_symlink_registration_exposes_only_consolidated_skills(self):
        skills = self.work / 'symlink-skills'
        install(source=PACKAGE, install_dir=self.installed, skills_dir=skills,
                skip_deps=True, registration='symlink')
        for name in SKILLS:
            self.assertTrue((skills / name).is_symlink())
            self.assertEqual((skills / name).resolve(), (self.installed / 'skills' / name).resolve())

    def test_zip_contains_only_manifest_files_and_runs_after_extraction(self):
        output = self.work / 'release.zip'
        result = export_release(output, self.installed)
        self.assertEqual(result['sha256'], sha(output))
        files = set(read(self.installed / 'release-files.json')) | {'release-files.json'}
        extracted = self.work / 'extracted'
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), {'android-harmony-migration-harness/' + f for f in files})
            archive.extractall(extracted)
        root = extracted / 'android-harmony-migration-harness'
        self.assertFalse((root / '.local').exists())
        self.assertFalse((root / '.venv').exists())
        self.assertEqual(integrity(root), [])
        result = self.cli(root, 'scripts/harness.py', 'capabilities')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['verified_project_capabilities'])
        original = output.read_bytes()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            export_release(output, self.installed)
        self.assertEqual(output.read_bytes(), original)

    def test_runtime_tampering_blocks_install_and_export_before_writing(self):
        source = self.installed / 'runtime/harness/project.py'
        original = source.read_bytes()
        source.write_bytes(original + b'\n# tampered\n')
        try:
            destination = self.work / 'tampered-install'
            with self.assertRaisesRegex(ValueError, 'integrity failed'):
                install(source=self.installed, install_dir=destination,
                        skills_dir=self.work / 'tampered-skills', skip_deps=True)
            self.assertFalse(destination.exists())
            output = self.work / 'tampered.zip'
            with self.assertRaisesRegex(ValueError, 'integrity failed'):
                export_release(output, self.installed)
            self.assertFalse(output.exists())
        finally:
            source.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
