"""Shared helpers for the relocatable team package; no host configuration."""
import hashlib
import json
from pathlib import Path
import sys
import os
import runpy

os.environ.setdefault("PYTHONUTF8", "1")
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ('android-to-harmony', 'android-to-harmony-feature-rollout', 'hmos-design-visual-mobile')
RETIRED_SKILLS = ('migration-harness-v2',)

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def release_version(root=ROOT):
    # 发布清单是安装目录、安装收据和导出收据的唯一版本来源。
    import re
    value = read(Path(root)/'.codex-plugin/plugin.json').get('version')
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise ValueError('发布清单需要有效的三段版本号')
    return value

def write(path, value):
    import os, uuid
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    try:
        with tmp.open('x',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)

def integrity(root=ROOT):
    root=Path(root).resolve();issues=[]
    try:expected=read(root/'release-files.json')
    except (OSError,ValueError) as exc:return ['release_manifest_unreadable:'+str(exc)]
    if not isinstance(expected,dict) or not expected:return ['release_manifest_invalid']
    for relative,wanted in expected.items():
        path=root/relative
        if (Path(relative).is_absolute() or '..' in Path(relative).parts or path.is_symlink()
            or root not in path.resolve().parents or not path.is_file() or sha(path)!=wanted):
            issues.append('release_file_mismatch:'+relative)
    return issues

def python_path(root=ROOT):
    configured=venv_python(Path(root)/'.venv')
    return str(configured) if configured.is_file() else sys.executable


def venv_python(env, system=None):
    return Path(env) / ('Scripts/python.exe' if (system or os.name) == 'nt' else 'bin/python')


def runtime_api(root=ROOT):
    return runpy.run_path(str(Path(root)/'runtime/harness/runtime.py'))


def configured_toolchain(root=ROOT, project=None):
    runtime=runtime_api(root)
    config=Path(root)/'.local/projects.json'
    entries=read(config).get('projects',[]) if config.is_file() else []
    if project is not None:
        entries=[p for p in entries if p.get('id')==project]
    layouts=[]
    for entry in entries:
        home=entry.get('deveco_home')
        layout=runtime['deveco_layout'](home) if home else None
        if layout:layouts.append(layout)
    return layouts


def configure_runtime_environment(root=ROOT):
    layouts=configured_toolchain(root)
    if layouts:
        os.environ['MIGRATION_HARNESS_DEVECO_TOOLS']=json.dumps(sorted({p['tools_root'] for p in layouts}))
        sdk_homes={p['sdk_home'] for p in layouts if p.get('sdk_home')}
        if len(sdk_homes)==1:os.environ.setdefault('DEVECO_SDK_HOME',next(iter(sdk_homes)))
