"""Keep command launchers and skill registrations on the same managed runtime."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PACKAGE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PACKAGE/'scripts'))

from common import SKILLS,read,sha,write,venv_python
from doctor import diagnose
from install import install,create_launcher,inspect_launcher,launcher_content,registration_matches
from runtime_discovery import discover,path_launcher_details


class LauncherInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='harness-launcher-')
        self.addCleanup(self.temp.cleanup)
        self.work=Path(self.temp.name).resolve()/'portable space & percent%'
        self.work.mkdir()
        self.source=self.make_package('source','1.0.0')
        self.destination=self.work/'installed'
        self.skills=self.work/'skills'
        self.bin=self.work/'bin'
        self.launcher=self.bin/('migration-harness.cmd' if os.name=='nt' else 'migration-harness')

    def make_package(self,name,version):
        package=self.work/name
        for skill in SKILLS:
            entry=package/'skills'/skill/'SKILL.md'
            entry.parent.mkdir(parents=True,exist_ok=True)
            entry.write_text('---\nname: '+skill+'\ndescription: fixture\n---\nFixture.\n',encoding='utf-8')
        write(package/'.codex-plugin/plugin.json',{'name':'android-harmony-migration-harness','version':version})
        write(package/'release-capabilities.json',{'format':'migration-harness/1'})
        script=package/'scripts/harness.py'
        script.parent.mkdir()
        script.write_text('import json,sys\nprint(json.dumps({"version":'+repr(version)+',"args":sys.argv[1:]}))\n',encoding='utf-8')
        runtime=package/'runtime/harness/migration.py'
        runtime.parent.mkdir(parents=True)
        runtime.write_text('"""Fixture runtime; discovery must never execute this file."""\n',encoding='utf-8')
        write(package/'release-files.json',{
            p.relative_to(package).as_posix():sha(p) for p in package.rglob('*') if p.is_file()
        })
        return package

    def install(self,**kwargs):
        values={'source':self.source,'install_dir':self.destination,'skills_dir':self.skills,
                'bin_dir':self.bin,'skip_deps':True,'registration':'pointer'}
        values.update(kwargs)
        return install(**values)

    def test_install_creates_an_executable_same_package_launcher_and_is_idempotent(self):
        first=self.install()
        self.assertEqual(Path(first['launcher']),self.launcher)
        self.assertEqual(inspect_launcher(self.launcher)['runtime'],str(self.destination/'scripts/harness.py'))
        self.assertTrue(registration_matches(self.skills/SKILLS[1],self.destination/'skills'/SKILLS[1]))
        if os.name!='nt':
            result=subprocess.run([str(self.launcher),'one','space & $literal'],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout),{'version':'1.0.0','args':['one','space & $literal']})
        before=self.launcher.read_bytes(),self.launcher.stat().st_mtime_ns
        second=self.install()
        self.assertEqual(before,(self.launcher.read_bytes(),self.launcher.stat().st_mtime_ns))
        self.assertIsNone(second['backups'])

    def test_custom_skills_install_defaults_to_a_local_sibling_bin(self):
        result=self.install(bin_dir=None)
        self.assertEqual(Path(result['bin_dir']),self.skills.parent/'bin')
        self.assertEqual(Path(result['launcher']),self.launcher)

    def test_legacy_managed_wrapper_is_backed_up_and_replaced_without_replace(self):
        self.install()
        old_content='#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(self.destination/'scripts/harness.py'))+' "$@"\n'
        if os.name=='nt':old_content=launcher_content(self.destination)
        self.launcher.write_text(old_content,encoding='utf-8')
        fresh=self.make_package('fresh-source','1.0.1')
        destination=self.work/'new-installed'
        result=self.install(source=fresh,install_dir=destination,skills_dir=self.work/'new-skills')
        self.assertEqual(inspect_launcher(self.launcher)['package'],str(destination))
        backup=Path(result['backups'])/'launchers'/self.launcher.name
        self.assertEqual(backup.read_text(encoding='utf-8'),old_content)

    def test_unknown_launcher_is_preserved_before_any_registration_change(self):
        self.bin.mkdir()
        self.launcher.write_text('custom tool; do not replace\n',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'--replace'):
            self.install()
        self.assertFalse(self.skills.exists())
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.launcher.read_text(),'custom tool; do not replace\n')
        result=self.install(replace=True)
        self.assertEqual((Path(result['backups'])/'launchers'/self.launcher.name).read_text(),'custom tool; do not replace\n')

    def test_launcher_failure_rolls_back_both_the_previous_command_and_skill(self):
        self.install()
        old=self.launcher.read_bytes()
        fresh=self.make_package('fresh-source','1.0.1')
        destination=self.work/'new-installed'
        def fail_after_creation(entry,content):
            create_launcher(entry,content)
            raise OSError('launcher failure')
        with patch('install.create_launcher',side_effect=fail_after_creation):
            with self.assertRaisesRegex(OSError,'launcher failure'):
                self.install(source=fresh,install_dir=destination,replace=True)
        self.assertEqual(self.launcher.read_bytes(),old)
        self.assertTrue(registration_matches(self.skills/SKILLS[1],self.destination/'skills'/SKILLS[1]))

    def test_new_launcher_failure_removes_new_registrations(self):
        with patch('install.create_launcher',side_effect=OSError('no command write')):
            with self.assertRaisesRegex(OSError,'no command write'):
                self.install()
        self.assertFalse(self.launcher.exists())
        self.assertFalse((self.skills/SKILLS[1]).exists())

    def test_doctor_detects_path_command_from_another_package_without_running_it(self):
        self.install()
        fresh=self.make_package('fresh-source','1.0.1')
        other=self.work/'new-installed'
        other_skills=self.work/'new-skills'
        self.install(source=fresh,install_dir=other,skills_dir=other_skills,bin_dir=self.work/'other-bin')
        selected=other_skills/SKILLS[1]/'SKILL.md'
        with patch('runtime_discovery.shutil.which',return_value=str(self.launcher)):
            result=discover(skills_dirs=[other_skills],selected_skill=selected)
        self.assertEqual(result['selection']['status'],'verified_package_runtime')
        self.assertEqual(result['path_launcher']['status'],'different_runtime')
        self.assertEqual(result['path_launcher']['expected_runtime'],str(other/'scripts/harness.py'))
        with patch('doctor.importlib.util.find_spec',return_value=True):
            report=diagnose(root=other,selected_skill=selected,path_launcher=self.launcher)
        self.assertIn('path_launcher_runtime_mismatch',report['issues'])
        self.assertEqual(report['status'],'needs_setup')
        self.assertFalse(result['path_launcher']['changes_performed'])

    def test_discovery_rejects_tampering_and_arbitrary_shell_without_execution(self):
        self.install()
        sentinel=self.work/'must-not-exist'
        runtime=self.destination/'runtime/harness/migration.py'
        runtime.write_text('from pathlib import Path\nPath('+repr(str(sentinel))+').touch()\n',encoding='utf-8')
        result=path_launcher_details(self.launcher,self.destination)
        self.assertEqual(result['status'],'runtime_not_verified')
        self.assertIn('runtime/harness/migration.py',result['changed_files'])
        self.launcher.write_text('#!/bin/sh\ntouch '+shlex.quote(str(sentinel))+'\n',encoding='utf-8')
        self.assertEqual(path_launcher_details(self.launcher,self.destination)['status'],'unmanaged')
        self.assertFalse(sentinel.exists())

    def test_discovery_rejects_missing_nonexecutable_and_nonpython_interpreters(self):
        self.install()
        original=self.launcher.read_text(encoding='utf-8')
        missing=self.work/'missing'/'python3'
        nonexecutable=self.work/'not-executable'/'python3'
        nonpython=self.work/'other-command'
        for candidate in (nonexecutable,nonpython):
            candidate.parent.mkdir(parents=True,exist_ok=True)
            candidate.write_text('must never execute',encoding='utf-8')
        nonexecutable.chmod(0o644);nonpython.chmod(0o755)
        candidates=(missing,nonpython) if os.name=='nt' else (missing,nonexecutable,nonpython)
        for candidate in candidates:
            with self.subTest(interpreter=str(candidate)):
                quote=(lambda value:'"'+str(value).replace('%','%%')+'"') if os.name=='nt' else (lambda value:shlex.quote(str(value)))
                self.launcher.write_text(original.replace(quote(sys.executable),quote(candidate)),encoding='utf-8')
                result=path_launcher_details(self.launcher,self.destination)
                self.assertEqual(result['status'],'unmanaged')
                self.assertIn('interpreter',result['reason'])

    def test_managed_launcher_binds_to_the_package_environment(self):
        self.install()
        configured=venv_python(self.destination/'.venv')
        configured.parent.mkdir(parents=True)
        configured.write_text('fixture executable; never invoked',encoding='utf-8')
        configured.chmod(0o755)
        result=path_launcher_details(self.launcher,self.destination)
        self.assertEqual(result['status'],'unmanaged')
        self.assertIn('package Python environment',result['reason'])
        self.launcher.write_text(launcher_content(self.destination),encoding='utf-8')
        result=path_launcher_details(self.launcher,self.destination)
        self.assertEqual(result['status'],'matches_runtime')
        self.assertEqual(result['interpreter'],str(configured))

    @unittest.skipIf(os.name=='nt','POSIX executable permissions')
    def test_symlink_to_nonexecutable_runtime_is_not_a_working_launcher(self):
        self.install()
        self.launcher.unlink()
        self.launcher.symlink_to(self.destination/'scripts/harness.py')
        result=path_launcher_details(self.launcher,self.destination)
        self.assertEqual(result['status'],'unmanaged')
        self.assertIn('not executable',result['reason'])

    def test_windows_wrapper_quotes_spaces_percent_and_disables_delayed_expansion(self):
        self.install()
        entry=self.work/'migration-harness.cmd'
        content=launcher_content(self.destination,system='nt')
        entry.write_text(content,encoding='utf-8')
        self.assertIn('setlocal DisableDelayedExpansion',content)
        self.assertIn('percent%%',content)
        self.assertEqual(inspect_launcher(entry)['runtime'],str(self.destination/'scripts/harness.py'))
        entry.write_text(content.replace('percent%%','percent%'),encoding='utf-8')
        self.assertFalse(inspect_launcher(entry)['managed'])


if __name__=='__main__':unittest.main()
