"""Inspect skill registrations and explicitly selected launchers without executing them."""
import shutil
from pathlib import Path
from common import read, sha, RETIRED_SKILLS
from install import registration_matches, inspect_launcher


def candidate(entry):
    entry = Path(entry).expanduser().absolute()
    result = {'entry': str(entry), 'selected_by_host': 'unknown'}
    try:
        target = entry.resolve(strict=True)
        marker = entry / '.harness-registration.json'
        if marker.is_file():
            target = Path(read(marker)['target']).resolve(strict=True)
            if not registration_matches(entry, target):
                raise ValueError('Registration marker or entry changed')
            result['registration'] = 'pointer'
        else:
            result['registration'] = 'symlink' if entry.is_symlink() else 'directory'
        result['skill'] = str(target / 'SKILL.md')
        result['skill_sha256'] = sha(target / 'SKILL.md')
        package = target.parent.parent
        launcher = package / 'scripts/harness.py'
        if target.parent.name != 'skills' or not launcher.is_file():
            result.update(status='unmanaged', reason='Runtime selection cannot be inferred from this skill')
            return result
        result.update(package_details(package), launcher=str(launcher))
        relative = (target / 'SKILL.md').relative_to(package).as_posix()
        if read(package / 'release-files.json').get(relative) != result['skill_sha256']:
            result.update(status='different', capabilities=None,
                          changed_files=sorted(set(result['changed_files']) | {relative}))
    except (OSError, ValueError, KeyError, TypeError) as error:
        result.update(status='invalid', reason=str(error))
    return result


def package_details(package):
    package = Path(package).resolve(strict=True)
    manifest = read(package / 'release-files.json')
    paths = ['scripts/harness.py', 'release-capabilities.json', '.codex-plugin/plugin.json',
             'skills/android-to-harmony-feature-rollout/SKILL.md']
    paths += [p for p in manifest if p.startswith('scripts/') and p.endswith('.py')]
    paths += [p for p in manifest if p.startswith('runtime/') and p.endswith('.py') and '/tests/' not in p and '/examples/' not in p]
    changed = [p for p in sorted(set(paths)) if p not in manifest or not (package / p).is_file() or sha(package / p) != manifest[p]]
    capabilities = read(package / 'release-capabilities.json') if not changed else None
    supported = capabilities and capabilities.get('format') == 'migration-harness/1'
    return {'status': 'different' if changed else ('verified_package_runtime' if supported else 'unsupported_package'), 'package': str(package),
            'version': read(package / '.codex-plugin/plugin.json')['version'], 'changed_files': changed,
            'runtime': str(package / 'scripts/harness.py'),
            'capabilities': capabilities}


def path_launcher_details(path=None,expected_package=None):
    path=path if path is not None else shutil.which('migration-harness')
    if not path:return {'status':'not_found','changes_performed':False}
    result=inspect_launcher(path)
    if not result['managed']:return result
    try:
        package=package_details(result['package'])
        result['runtime_verification']=package['status']
        result['version']=package['version']
        result['changed_files']=package['changed_files']
        if expected_package is not None:
            expected=Path(expected_package).resolve()/'scripts/harness.py'
            result['expected_runtime']=str(expected)
            result['status']='matches_runtime' if result['runtime']==str(expected) else 'different_runtime'
        if package['status']!='verified_package_runtime' and result['status']!='different_runtime':
            result['status']='runtime_not_verified'
    except (OSError,ValueError,KeyError,TypeError) as error:
        result.update(status='runtime_not_verified',reason=str(error))
    return result


def discover(skills_dirs=None, selected_skill=None, launcher=None, run_dir=None, required_capabilities=(),path_launcher=None):
    if selected_skill and launcher:
        raise ValueError('Select either a skill or a launcher to identify the active host entry')
    directories = skills_dirs if skills_dirs is not None else [Path.home() / '.agents/skills', Path.home() / '.codex/skills']
    entries = [Path(root) / name for root in directories
               for name in ('android-to-harmony-feature-rollout', *RETIRED_SKILLS)
               if (Path(root) / name / 'SKILL.md').is_file()]
    if selected_skill:
        selected = Path(selected_skill).expanduser()
        selected = selected.parent if selected.name == 'SKILL.md' else selected
        if selected.absolute() not in [e.absolute() for e in entries]:
            entries.append(selected)
    candidates = [candidate(entry) for entry in entries]
    selection = None
    if selected_skill:
        selection = next(c for c in candidates if c['entry'] == str(selected.absolute()))
        selection['selected_by_host'] = 'explicit_argument'
    if launcher:
        try:
            path = Path(launcher).expanduser().resolve(strict=True)
            if path.name != 'harness.py' or path.parent.name != 'scripts':
                raise ValueError('Select the team-package scripts/harness.py launcher')
            selection = {**package_details(path.parent.parent), 'launcher': str(path), 'selected_by_host': 'explicit_argument'}
        except (OSError, ValueError, KeyError, TypeError) as error:
            selection = {'status': 'invalid', 'reason': str(error), 'launcher': str(launcher)}
    for value in candidates + ([selection] if selection and selection not in candidates else []):
        capabilities = value.get('capabilities') or {}
        value['missing_capabilities'] = [key for key in required_capabilities if capabilities.get(key) is not True]
        if value['missing_capabilities'] and value['status'] == 'verified_package_runtime':
            value['status'] = 'missing_capabilities'
    receipts = []
    if run_dir:
        state = read(Path(run_dir) / 'harness-state.json')
        if state.get('format') != 'migration-harness/1':
            raise ValueError('Unsupported run format')
        for tid, task in state.get('tasks', {}).items():
            if not task.get('receipt'):
                continue
            ref = task['receipt']
            try:
                if sha(Path(ref['path'])) != ref['sha256']:
                    raise ValueError('Task receipt changed')
                receipt = read(ref['path'])
                engines = receipt['inputs'].get('engine_files', [])
                available = all(Path(item['path']).is_file() and sha(Path(item['path'])) == item['sha256'] for item in engines)
                receipts.append({'task_id': tid, 'engine_records': engines, 'original_runtime_available': bool(engines) and available})
            except (OSError, ValueError, KeyError, TypeError) as error:
                receipts.append({'task_id': tid, 'status': 'invalid', 'reason': str(error)})
    command=path_launcher_details(path_launcher,selection.get('package') if selection else None)
    return {'candidates': candidates, 'selection': selection, 'selection_status': 'explicit' if selection else 'host_selection_unknown',
            'path_launcher':command,
            'run_receipts': receipts, 'changes_performed': False,
            'note': 'Discovery is not proof of host selection. Pass --selected-skill or --launcher from the active host.'}
