#!/usr/bin/env python3
"""Route an explicitly configured Harmony target to the bundled project profile."""
import argparse,json,sys,runpy
from pathlib import Path
SKILL_ROOT=Path(__file__).resolve().parent.parent
CONFIG_PATH=SKILL_ROOT.parent.parent/'.local/projects.json'

def resolve_project(candidate_path,config_path=None):
    path=Path(config_path or CONFIG_PATH)
    if not path.is_file():return None
    config=json.loads(path.read_text(encoding='utf-8'));candidate=Path(candidate_path).expanduser().resolve()
    matches=[]
    for project in config['projects']:
        for configured in project['target_roots']:
            root=Path(configured).expanduser().resolve()
            if candidate==root or root in candidate.parents:matches.append((len(root.parts),project,root))
    if not matches:return None
    longest=max(m[0] for m in matches);choices=[m for m in matches if m[0]==longest]
    if len({m[1]['id'] for m in choices})>1:raise ValueError('Ambiguous configured Harmony roots; correct .local/projects.json')
    _,project,matched=choices[0];profile=(SKILL_ROOT/project['profile']).resolve()
    if SKILL_ROOT not in profile.parents or not profile.is_file():raise ValueError('Configured profile is not a bundled readable file')
    core=SKILL_ROOT.parent.parent/'runtime/harness/runtime.py'
    home=project.get('deveco_home')
    tools=runpy.run_path(str(core))['deveco_layout'](home) if home else None
    knowledge=str((matched/'knowledge-base').resolve()) if (matched/'knowledge-base').is_dir() else project.get('knowledge_root')
    return {'toolchain':tools,'project':project['id'],'profile':str(profile),'match_kind':'target','matched_root':str(matched),'candidate':str(candidate),
            'configuration':str(path.resolve()),'resolved_paths':{'HARMONY_ROOT':str(matched),
                'ANDROID_ROOT':project['source_roots'][0],'BUILD_CWD':project['build_cwd'],
                'ANALYSIS_OUTPUT_DIR':project['analysis_output_dir'],'DEVECO_HOME':project['deveco_home'],
                'KNOWLEDGE_ROOT':knowledge}}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--path',default=str(Path.cwd()));p.add_argument('--config');a=p.parse_args()
    try:
        result=resolve_project(a.path,a.config)
        print(json.dumps({'status':'matched' if result else 'unmatched',**(result or {'message':'先执行完整包 scripts/configure.py 配置本机 Harmony/Android 路径。'})},ensure_ascii=False,indent=2))
        raise SystemExit(0 if result else 2)
    except (ValueError,OSError,KeyError) as exc:print(str(exc),file=sys.stderr);raise SystemExit(2)
