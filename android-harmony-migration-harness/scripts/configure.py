#!/usr/bin/env python3
"""Bind this colleague's actual projects without modifying packaged profiles."""
import argparse,json,sys
from pathlib import Path
from common import ROOT,read,write,runtime_api
PROJECTS={'interactive-demo':'.','dual-platform':'.','host-bridge':'.'}

def configure(project,harmony_root,android_root,build_cwd=None,deveco_home=None,root=ROOT):
    if project not in PROJECTS:raise ValueError('Unsupported profile; do not infer an unrelated project')
    harmony=Path(harmony_root).expanduser().resolve(strict=True)
    android=Path(android_root).expanduser().resolve(strict=True)
    build=Path(build_cwd).expanduser().resolve(strict=True) if build_cwd else (harmony/PROJECTS[project]).resolve(strict=True)
    if not all(p.is_dir() for p in (harmony,android,build)):raise ValueError('Project paths must be existing directories')
    if build!=harmony and harmony not in build.parents:raise ValueError('build-cwd must be within Harmony root')
    runtime=runtime_api(root)
    candidates=runtime['default_deveco_homes']()
    discovered=next((p for p in candidates if runtime['deveco_layout'](p)),None)
    sdk=Path(deveco_home or discovered).expanduser().resolve() if (deveco_home or discovered) else None
    entry={'id':project,'profile':'profiles/'+project+'.md','target_roots':[str(harmony)],'source_roots':[str(android)],
           'build_cwd':str(build),'analysis_output_dir':str(harmony/'docs/migration-cases'),'deveco_home':str(sdk) if sdk else None,
           'knowledge_root':None}
    path=Path(root)/'.local/projects.json'
    value=read(path) if path.exists() else {'version':1,'projects':[]}
    value['projects']=[p for p in value['projects'] if p['id']!=project]+[entry]
    write(path,value)
    return {'status':'project_configured','project':entry,'configuration':str(path),'quality_gate_granted':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',choices=PROJECTS,required=True)
    p.add_argument('--harmony-root',required=True);p.add_argument('--android-root',required=True)
    p.add_argument('--build-cwd');p.add_argument('--deveco-home');a=p.parse_args()
    try:print(json.dumps(configure(a.project,a.harmony_root,a.android_root,a.build_cwd,a.deveco_home),ensure_ascii=False,indent=2))
    except (ValueError,OSError) as exc:print(str(exc),file=sys.stderr);raise SystemExit(2)
