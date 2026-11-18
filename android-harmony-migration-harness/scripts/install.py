#!/usr/bin/env python3
"""Install the complete team package; preserve conflicts unless --replace is explicit."""
import argparse,json,os,re,shlex,shutil,sys,tempfile,uuid
from pathlib import Path
from common import ROOT,SKILLS,RETIRED_SKILLS,release_version,integrity,read,write,sha,python_path,venv_python
from bootstrap import setup


LAUNCHER_MARKER = 'migration-harness-team managed launcher v1'


def launcher_content(package,system=None):
    system=system or os.name
    package=Path(package).resolve()
    python=python_path(package)
    script=str(package/'scripts/harness.py')
    if any('\n' in value or '\r' in value for value in (python,script)):
        raise ValueError('Launcher paths cannot contain newlines')
    if system=='nt':
        if any('"' in value for value in (python,script)):
            raise ValueError('Windows launcher paths cannot contain double quotes')
        python,script=(value.replace('%','%%') for value in (python,script))
        return '@echo off\nrem '+LAUNCHER_MARKER+'\nsetlocal DisableDelayedExpansion\n"'+python+'" "'+script+'" %*\n'
    return '#!/bin/sh\n# '+LAUNCHER_MARKER+'\nexec '+shlex.quote(python)+' '+shlex.quote(script)+' "$@"\n'


def inspect_launcher(entry):
    """Recognize only our exact wrappers or a symlink to a managed package."""
    entry=Path(entry).expanduser().absolute()
    result={'path':str(entry),'managed':False,'changes_performed':False}
    if not entry.exists() and not entry.is_symlink():return {**result,'status':'not_found'}
    try:
        if entry.is_symlink():
            result['link_target']=os.readlink(entry)
            script=entry.resolve(strict=True)
            if os.name!='nt' and not os.access(script,os.X_OK):
                raise ValueError('Launcher symlink target is not executable')
            kind='symlink'
        else:
            if not entry.is_file() or entry.stat().st_size>16384:
                raise ValueError('Launcher is not a recognized wrapper')
            content=entry.read_text(encoding='utf-8')
            result['sha256']=sha(entry)
            lines=[line.strip() for line in content.splitlines() if line.strip()]
            if lines and lines[0]=='#!/bin/sh':
                commands=[line for line in lines[1:] if not line.startswith('#')]
                if len(commands)!=1:raise ValueError('Launcher contains additional commands')
                argv=shlex.split(commands[0])
                if len(argv)!=4 or argv[0]!='exec' or argv[3]!='$@' or not Path(argv[1]).is_absolute():
                    raise ValueError('Launcher is not a recognized Python wrapper')
                if commands[0]!='exec '+shlex.quote(argv[1])+' '+shlex.quote(argv[2])+' "$@"':
                    raise ValueError('Launcher shell quoting is not a recognized literal wrapper')
                interpreter=Path(argv[1]);script=Path(argv[2]);kind='posix_wrapper'
                marked=('# '+LAUNCHER_MARKER) in lines
            elif (len(lines)==4 and lines[0].lower()=='@echo off' and
                  lines[1]=='rem '+LAUNCHER_MARKER and lines[2]=='setlocal DisableDelayedExpansion'):
                match=re.fullmatch(r'"([^"\r\n]+)" "([^"\r\n]+)" %\*',lines[3])
                if not match:raise ValueError('Launcher is not a recognized Windows wrapper')
                if any(value.replace('%%','%').replace('%','%%')!=value for value in match.groups()):
                    raise ValueError('Launcher paths contain variable expansion')
                interpreter=Path(match.group(1).replace('%%','%'))
                script=Path(match.group(2).replace('%%','%'));kind='windows_wrapper';marked=True
            else:raise ValueError('Launcher is not a managed Harness wrapper')
        if not script.is_absolute() or script.name!='harness.py' or script.parent.name!='scripts':
            raise ValueError('Launcher does not target a team-package scripts/harness.py')
        script=script.resolve(strict=True)
        package=script.parent.parent
        if kind!='symlink':
            if (not interpreter.is_absolute() or not interpreter.is_file() or
                not os.access(interpreter,os.X_OK) or
                not re.fullmatch(r'python(?:3(?:\.[0-9]+)*)?(?:\.exe)?',interpreter.name,re.IGNORECASE)):
                raise ValueError('Launcher Python interpreter is missing or not executable Python')
            configured=venv_python(package/'.venv')
            if marked and configured.is_file() and interpreter.absolute()!=configured.absolute():
                raise ValueError('Managed launcher does not use the package Python environment')
            result['interpreter']=str(interpreter)
        manifest=read(package/'release-files.json')
        plugin=read(package/'.codex-plugin/plugin.json')
        if (plugin.get('name')!='android-harmony-migration-harness' or
            manifest.get('scripts/harness.py')!=sha(script)):
            raise ValueError('Launcher target is not an intact managed package entry')
        return {**result,'status':'managed','managed':True,'kind':kind,
                'runtime':str(script),'package':str(package),'sha256':sha(entry)}
    except (OSError,ValueError,KeyError,TypeError,UnicodeError) as error:
        return {**result,'status':'unmanaged','reason':str(error)}


def create_launcher(entry,content):
    entry.parent.mkdir(parents=True,exist_ok=True)
    handle,temporary=tempfile.mkstemp(prefix='.harness-launcher-',dir=entry.parent)
    prepared=Path(temporary)
    try:
        with os.fdopen(handle,'w',encoding='utf-8',newline='\n') as stream:
            stream.write(content);stream.flush();os.fsync(stream.fileno())
        prepared.chmod(0o755)
        # link refuses to overwrite an entry that appeared after preflight.
        os.link(prepared,entry)
    finally:
        prepared.unlink(missing_ok=True)


def registration_matches(entry,target):
    if entry.is_symlink():return entry.resolve()==target.resolve()
    marker=entry/'.harness-registration.json'
    if not marker.is_file() or marker.is_symlink():return False
    try:
        receipt=read(marker)
        return (receipt.get('target')==str(target.resolve()) and
                receipt.get('entry_sha256')==sha(entry/'SKILL.md') and
                receipt.get('source_sha256')==sha(target/'SKILL.md') and
                {p.name for p in entry.iterdir()}=={'SKILL.md','.harness-registration.json'})
    except (OSError,ValueError,TypeError):return False


def create_registration(entry,target,mode):
    if mode=='symlink':
        entry.symlink_to(target,target_is_directory=True)
        return
    content=(target/'SKILL.md').read_text(encoding='utf-8')
    if not content.startswith('---\n') or '\n---' not in content[4:]:
        raise ValueError('Bundled skill has no frontmatter')
    end=content.index('\n---',4)+4
    pointer=(content[:end]+'\n\n这是已安装团队包的转发入口。执行前必须读取唯一技能正文：\n\n'
             +str(target.resolve()/'SKILL.md')+'\n\n其中的相对引用以该正文目录为基准。'
             '核心、其他技能和资源均使用原团队包；此入口不保存引擎副本。\n')
    prepared=Path(tempfile.mkdtemp(prefix='.harness-register-',dir=entry.parent))
    try:
        (prepared/'SKILL.md').write_text(pointer,encoding='utf-8')
        write(prepared/'.harness-registration.json',{'target':str(target.resolve()),
              'source_sha256':sha(target/'SKILL.md'),'entry_sha256':sha(prepared/'SKILL.md')})
        prepared.rename(entry)
    finally:
        if prepared.exists():shutil.rmtree(prepared)


def remove_registration(entry,target):
    if entry.is_symlink() and entry.resolve()==target.resolve():entry.unlink()
    elif registration_matches(entry,target):shutil.rmtree(entry)


def install(source=ROOT,install_dir=None,skills_dir=None,replace=False,skip_deps=False,wheelhouse=None,legacy_skills_dir=None,register_only=False,registration=None,bin_dir=None):
    source=Path(source).resolve()
    if integrity(source):raise ValueError('Release integrity failed; no installation was performed')
    version=release_version(source)
    if register_only and install_dir:
        raise ValueError('register-only keeps the extracted location; omit install-dir')
    destination=source if register_only else Path(install_dir or Path.home()/'.local/share/migration-harness-team'/version).expanduser().absolute()
    registration=registration or ('pointer' if os.name=='nt' else 'symlink')
    if registration not in ('pointer','symlink'):raise ValueError('Unknown registration mode')
    skills=Path(skills_dir or Path.home()/'.agents/skills').expanduser().absolute()
    binaries=Path(bin_dir or (skills.parent/'bin' if skills_dir else Path.home()/'.local/bin')).expanduser().absolute()
    launcher=binaries/('migration-harness.cmd' if os.name=='nt' else 'migration-harness')
    for p in (destination,skills,binaries):
        if p in (Path(p.anchor),Path.home()):raise ValueError('Use a dedicated install/skills directory')
    if destination==skills or destination in skills.parents or skills in destination.parents:
        raise ValueError('Release and skill discovery directories must be separate')
    if binaries==destination or destination in binaries.parents or binaries==skills or skills in binaries.parents:
        raise ValueError('Use a launcher directory outside the release and skill discovery directories')
    legacy=Path(legacy_skills_dir).expanduser().absolute() if legacy_skills_dir else (Path.home()/'.codex/skills' if skills_dir is None else None)
    legacy_conflicts=[legacy/name for name in (*SKILLS,*RETIRED_SKILLS)
                      if legacy is not None and legacy.resolve()!=skills.resolve()
                      and (legacy/name/'SKILL.md').is_file()]
    retired=[skills/name for name in RETIRED_SKILLS if (skills/name).exists() or (skills/name).is_symlink()]
    # Inspect every conflict before writing any registration.
    conflicts=[]
    for name in SKILLS:
        entry=skills/name;target=destination/'skills'/name
        if registration_matches(entry,target):continue
        if entry.exists() or entry.is_symlink():conflicts.append(entry)
    conflicts.extend(legacy_conflicts)
    conflicts.extend(retired)
    prior_launcher=inspect_launcher(launcher)
    if prior_launcher['status']!='not_found' and not prior_launcher['managed']:
        conflicts.append(launcher)
    if conflicts and not replace:
        raise ValueError('Existing skills or launcher preserved; review and use --replace to back up and replace: '+', '.join(map(str,conflicts)))
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir() or integrity(destination):
            raise ValueError('Install destination exists but is not this intact managed release; choose a new install-dir')
        if sha(destination/'release-files.json')!=sha(source/'release-files.json'):
            raise ValueError('Different release at destination; choose a new install-dir')
    else:
        destination.parent.mkdir(parents=True,exist_ok=True)
        prepared=Path(tempfile.mkdtemp(prefix='.prepare-harness-',dir=destination.parent))
        for relative in read(source/'release-files.json'):
            target=prepared/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source/relative,target)
        shutil.copy2(source/'release-files.json',prepared/'release-files.json')
        if integrity(prepared):raise ValueError('Prepared copy changed; retained for inspection: '+str(prepared))
        prepared.rename(destination)
    # Dependencies complete before registrations change. A failed setup is resumable.
    if not skip_deps:setup(destination,wheelhouse)
    content=launcher_content(destination)
    skills.mkdir(parents=True,exist_ok=True)
    backup=destination.parent/'backups'/uuid.uuid4().hex
    changes=[]
    try:
        for entry in legacy_conflicts:
            saved=backup/'legacy'/entry.name;saved.parent.mkdir(parents=True,exist_ok=True)
            entry.rename(saved);changes.append((entry,saved,'skill'))
        for entry in retired:
            saved=backup/'modern'/entry.name;saved.parent.mkdir(parents=True,exist_ok=True)
            entry.rename(saved);changes.append((entry,saved,'skill'))
        for name in SKILLS:
            entry=skills/name;target=destination/'skills'/name
            if registration_matches(entry,target):continue
            prior=None
            if entry.exists() or entry.is_symlink():
                if not replace:raise ValueError('Skill appeared during install; refusing overwrite')
                prior=backup/'modern'/name;prior.parent.mkdir(parents=True,exist_ok=True);entry.rename(prior)
            changes.append((entry,prior,'skill'))
            create_registration(entry,target,registration)
        launcher_matches=(launcher.is_file() and not launcher.is_symlink() and
                          launcher.read_bytes()==content.encode('utf-8') and
                          (os.name=='nt' or os.access(launcher,os.X_OK)))
        if not launcher_matches:
            prior=None
            if launcher.exists() or launcher.is_symlink():
                current=inspect_launcher(launcher)
                if current!=prior_launcher:
                    raise ValueError('Launcher changed during install; refusing overwrite')
                if not current['managed'] and not replace:
                    raise ValueError('Unmanaged launcher preserved; use --replace to back up and replace')
                prior=backup/'launchers'/launcher.name
                prior.parent.mkdir(parents=True,exist_ok=True);launcher.rename(prior)
            changes.append((launcher,prior,'launcher'))
            create_launcher(launcher,content)
    except BaseException:
        for entry,prior,kind in reversed(changes):
            if kind=='launcher':
                if entry.is_file() and not entry.is_symlink() and entry.read_bytes()==content.encode('utf-8'):
                    entry.unlink()
            else:remove_registration(entry,destination/'skills'/entry.name)
            if prior is not None and not entry.exists() and not entry.is_symlink():prior.rename(entry)
        raise
    result={'status':'registered' if register_only else 'installed','registration':registration,'version':version,'installation':str(destination),'skills_dir':str(skills),
            'skills':list(SKILLS),'retired_skills':list(RETIRED_SKILLS),
            'bin_dir':str(binaries),'launcher':str(launcher),'launcher_runtime':str(destination/'scripts/harness.py'),
            'dependencies':'not_checked' if skip_deps else 'ready',
            'backups':str(backup) if backup.exists() else None,'next':'Open a new Codex task, then configure a real project'}
    previous=destination/'.local/install-receipt.json'
    if previous.exists():
        history=destination/'.local/install-history'/str(uuid.uuid4());history.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(previous,history)
    write(previous,result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--register-only',action='store_true',help='Register the extracted package without copying it')
    p.add_argument('--registration',choices=['pointer','symlink'],help='Default: pointer on Windows, symlink on POSIX')
    p.add_argument('--install-dir');p.add_argument('--skills-dir');p.add_argument('--replace',action='store_true')
    p.add_argument('--bin-dir',help='Launcher directory; defaults to ~/.local/bin, or beside a custom skills-dir')
    p.add_argument('--skip-deps',action='store_true',help='Use an already managed Python environment; doctor still validates it')
    p.add_argument('--legacy-skills-dir',help='Override the legacy skill root when testing or using a custom installation')
    p.add_argument('--wheelhouse',help='Optional predownloaded wheels for offline dependency setup')
    a=p.parse_args()
    try:print(json.dumps(install(install_dir=a.install_dir,skills_dir=a.skills_dir,replace=a.replace,skip_deps=a.skip_deps,wheelhouse=a.wheelhouse,legacy_skills_dir=a.legacy_skills_dir,register_only=a.register_only,registration=a.registration,bin_dir=a.bin_dir),ensure_ascii=False,indent=2))
    except (ValueError,OSError) as exc:print(str(exc),file=sys.stderr);raise SystemExit(2)
