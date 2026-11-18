#!/usr/bin/env python3
"""Prepare an isolated Python environment from the tested dependency lock."""
import argparse,json,subprocess,sys
from pathlib import Path
from common import ROOT,integrity,write,venv_python

def setup(root=ROOT,wheelhouse=None):
    root=Path(root).resolve()
    if integrity(root):raise ValueError('Package integrity failed; unpack a clean release')
    if not ((3,9)<=sys.version_info[:2]<(3,13)):
        raise ValueError('This dependency lock supports Python 3.9–3.12; select that interpreter')
    env=root/'.venv';py=venv_python(env)
    if env.exists() and not py.is_file():raise ValueError('Incomplete .venv; move it aside and retry')
    if not py.is_file():subprocess.run([sys.executable,'-m','venv',str(env)],check=True,timeout=120)
    command=[str(py),'-m','pip','install','--disable-pip-version-check','--only-binary=:all:',
             '-r',str(root/'requirements.txt')]
    if wheelhouse:
        wheels=Path(wheelhouse).expanduser().resolve(strict=True)
        command.extend(['--no-index','--find-links',str(wheels)])
    log=root/'.local/dependencies.log';log.parent.mkdir(exist_ok=True)
    with log.open('w',encoding='utf-8') as stream:
        process=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,timeout=600)
    if process.returncode:raise ValueError('Dependency setup failed; inspect '+str(log))
    result={'status':'dependencies_ready','python':str(py),'requirements_sha256':__import__('common').sha(root/'requirements.txt')}
    write(root/'.local/dependencies.json',result)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--wheelhouse');args=parser.parse_args()
    try:print(json.dumps(setup(wheelhouse=args.wheelhouse),ensure_ascii=False,indent=2))
    except (ValueError,OSError,subprocess.SubprocessError) as exc:print(str(exc),file=sys.stderr);raise SystemExit(2)
