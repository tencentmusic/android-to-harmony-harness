#!/usr/bin/env python3
"""Check release, bundled skills, dependencies and optionally a configured project."""
import argparse,importlib.util,json,os,platform,shutil,sys,subprocess
from pathlib import Path
# Also allow runpy from the canonical maintenance tests.
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import ROOT,SKILLS,integrity,read,python_path,runtime_api,sha
from runtime_discovery import discover as discover_runtime, candidate, path_launcher_details


def installed_engine(root, skills_dir=None):
    entry = Path(skills_dir or Path.home()/'.agents/skills')/'android-to-harmony-feature-rollout'
    if not (entry/'SKILL.md').is_file():
        return {'status':'not_found','changes_performed':False}
    selected = candidate(entry)
    expected = read(Path(root)/'release-files.json')
    scripts = Path(selected.get('package', entry))
    expected = {name: value for name, value in expected.items() if name.startswith('runtime/harness/')}
    different = [name for name, wanted in expected.items() if not (scripts/name).is_file() or sha(scripts/name)!=wanted]
    if selected['status']!='verified_package_runtime':
        different.append('unverified_registration')
    return {'status':'different' if different else 'matches_package','different_modules':different,
            'changes_performed':False,'next':'Review version differences before installing; preserve original business receipts'}

def diagnose(root=ROOT,project=None,selected_skill=None,launcher=None,run_dir=None,required_capabilities=(),
             agent_adapter=None,require_agent_enforcement=False,path_launcher=None):
    root=Path(root).resolve();issues=integrity(root)
    package_root=root
    discovered=discover_runtime(selected_skill=selected_skill,launcher=launcher,run_dir=run_dir,
                                required_capabilities=required_capabilities,path_launcher=path_launcher)
    command=discovered['path_launcher']
    if not discovered['selection']:
        command=path_launcher_details(path_launcher,root)
        discovered['path_launcher']=command
    if command['status']=='different_runtime':issues.append('path_launcher_runtime_mismatch')
    elif command['status'] in ('unmanaged','runtime_not_verified'):
        issues.append('path_launcher_not_verified:'+command['status'])
    if required_capabilities and not discovered['selection']:
        issues.append('runtime_selection_required')
    if discovered['selection'] and discovered['selection']['status']!='verified_package_runtime':
        issues.append('selected_runtime_not_verified:'+discovered['selection']['status'])
    agent_integration={'status':'not_checked','active_enforcement':False}
    if agent_adapter:
        try:
            if integrity(root):raise ValueError('Verify package integrity before inspecting an Agent adapter')
            spec=importlib.util.spec_from_file_location('harness_agent_adapter_inspection',root/'runtime/harness/agent_adapter.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            agent_integration=module.inspect_adapter(agent_adapter,require_agent_enforcement)
            if require_agent_enforcement and not agent_integration['enforcement_available']:issues.append('agent_enforcement_unavailable')
        except (OSError,ValueError,KeyError,TypeError) as error:
            agent_integration={'status':'invalid','reason':str(error),'active_enforcement':False}
            issues.append('agent_adapter_invalid')
    elif require_agent_enforcement:
        issues.append('agent_adapter_required')
    dependencies={name:importlib.util.find_spec(name) is not None for name in ('PIL','numpy','skimage','json5')}
    skills={name:(root/'skills'/name/'SKILL.md').is_file() for name in SKILLS}
    if platform.system() not in ('Darwin','Linux','Windows'):issues.append('core_runtime_unsupported_platform')
    if not ((3,9)<=sys.version_info[:2]<(3,13)):issues.append('tested_python_3_9_through_3_12_required')
    if not shutil.which('git'):issues.append('git_missing')
    for name,found in dependencies.items():
        if not found:issues.append(('runtime_dependency_missing:' if name=='json5' else 'visual_dependency_missing:')+name)
    for name,found in skills.items():
        if not found:issues.append('bundled_skill_missing:'+name)
    project_issues=[];selected=None;toolchain=None;knowledge_hint=None
    if project:
        config=root/'.local/projects.json'
        entries=read(config).get('projects',[]) if config.exists() else []
        selected=next((p for p in entries if p['id']==project),None)
        if not selected:project_issues.append('project_not_configured')
        else:
            for key in ('target_roots','source_roots'):
                if not selected.get(key) or not all(Path(v).is_dir() for v in selected[key]):project_issues.append(key+'_missing')
            build=Path(selected.get('build_cwd',''))
            for name in ('build-profile.json5','hvigorfile.ts'):
                if not (build/name).is_file():project_issues.append('build_file_missing:'+name)
            home=selected.get('deveco_home')
            toolchain=runtime_api(root)['deveco_layout'](home) if home else None
            if not toolchain:project_issues.append('deveco_node_or_hvigor_not_found')
            for root in selected.get('target_roots',[]):
                knowledge=Path(root)/'knowledge-base'
                if knowledge.is_dir():
                    knowledge_hint=str(knowledge.resolve())
                    break
            if not knowledge_hint and selected.get('knowledge_root'):
                custom=Path(selected['knowledge_root'])
                if custom.is_dir():knowledge_hint=str(custom.resolve())
            if not knowledge_hint:project_issues.append('knowledge_root_not_found:configure knowledge_root when a project uses an external knowledge base')
    return {'status':'core_ready' if not issues else 'needs_setup','issues':issues,
        'release_integrity':'failed' if any(i.startswith('release_') for i in issues) else 'passed',
        'platform':platform.system(),'platform_validation':'Windows native adapted; macOS regression verified; native Windows acceptance pending',
        'python':sys.executable,'visual_dependencies':{k:v for k,v in dependencies.items() if k!='json5'},
        'runtime_dependencies':{'json5':dependencies['json5']},'team_skills':skills,
        'project_status':('needs_setup' if project_issues else 'paths_ready') if project else 'not_checked',
        'project_issues':project_issues,'project':selected,'toolchain':toolchain,'knowledge_root':knowledge_hint,'device_accounts':'not_checked',
        'optional_tools':{name:shutil.which(name) for name in ('adb','hdc','rg','ffmpeg')},
        'installed_engine':installed_engine(package_root),
        'runtime_discovery':discovered,
        'agent_integration':agent_integration,
        'quality_gate_granted':False,'background_wakeup':False}

if __name__=='__main__':
    chosen=python_path()
    if Path(chosen).absolute()!=Path(sys.executable).absolute():raise SystemExit(subprocess.call([chosen,str(Path(__file__).resolve()),*sys.argv[1:]]))
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project')
    entry=p.add_mutually_exclusive_group();entry.add_argument('--selected-skill');entry.add_argument('--launcher');p.add_argument('--run-dir')
    p.add_argument('--require-capability',action='append',default=[])
    p.add_argument('--path-launcher',help='Inspect this command entry instead of searching PATH; never executes it')
    p.add_argument('--agent-adapter');p.add_argument('--require-agent-enforcement',action='store_true');a=p.parse_args()
    try:
        result=diagnose(project=a.project,selected_skill=a.selected_skill,launcher=a.launcher,run_dir=a.run_dir,
                        required_capabilities=a.require_capability,agent_adapter=a.agent_adapter,
                        require_agent_enforcement=a.require_agent_enforcement,path_launcher=a.path_launcher);print(json.dumps(result,ensure_ascii=False,indent=2))
        raise SystemExit(0 if result['status']=='core_ready' and not result['project_issues'] else 2)
    except (ValueError,OSError) as exc:print(str(exc),file=sys.stderr);raise SystemExit(2)
