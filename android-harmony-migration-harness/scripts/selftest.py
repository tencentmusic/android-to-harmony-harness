#!/usr/bin/env python3
"""Offline portability checks in temporary directories, without business code or devices."""
import argparse,json,os,runpy,subprocess,sys,tempfile,time
from pathlib import Path
from common import ROOT,python_path,integrity,runtime_api,write


def selftest(root=ROOT):
    root=Path(root).resolve();runtime=runtime_api(root);checks=[]
    def check(name,fn):
        try:
            fn();checks.append({'check':name,'status':'passed'})
        except Exception as exc:
            checks.append({'check':name,'status':'failed','detail':str(exc)})
    def require(value,message):
        if not value:raise AssertionError(message)
    def cli(*args):
        p=subprocess.run([sys.executable,str(root/'scripts/harness.py'),*map(str,args)],
                         capture_output=True,encoding='utf-8',timeout=20)
        return p,json.loads(p.stdout) if p.stdout.strip() else {}
    check('release_integrity',lambda:require(not integrity(root),'Release bytes differ'))
    with tempfile.TemporaryDirectory(prefix='harness-selftest-') as directory:
        work=Path(directory)/'中文 space & literal';work.mkdir()
        core=root/'runtime/harness/runtime.py'
        def unicode_state():
            p=work/'状态.json';value={'原因':'中文，原始字节','path':str(work)}
            runtime['write_json'](p,value)
            require(runtime['load'](p)==value,'Unicode state changed')
        check('utf8_state_and_paths',unicode_state)
        def capabilities():
            p,value=cli('capabilities')
            require(p.returncode==0,p.stderr)
            require(value['format']=='migration-harness/1','Unified protocol changed')
            require(value==json.loads((root/'release-capabilities.json').read_text(encoding='utf-8')),
                    'Release capabilities differ from runtime')
            require(all(value.get(key) is True for key in ('joint_review','continuation_advice',
                        'change_driven_test_plan','verified_project_capabilities','managed_agent_runner')),'Capability missing')
        check('portable_unified_cli',capabilities)
        def reviewed_demo():
            demo=work/'reviewed-demo'
            p=subprocess.run([sys.executable,str(root/'runtime/harness/examples/demo.py'),
                              '--output',str(demo)],capture_output=True,encoding='utf-8',timeout=30,cwd=work)
            require(p.returncode==0,p.stderr)
            p=subprocess.run([sys.executable,str(root/'scripts/harness.py'),'verify',
                              '--run-dir',str(demo/'run')],capture_output=True,encoding='utf-8',timeout=20,cwd=work)
            require(p.returncode==1 and json.loads(p.stdout)['status']=='demo_only',
                    'Synthetic run was promoted to real delivery: '+p.stderr)
        check('demo_never_grants_real_delivery',reviewed_demo)
        def visual_pixels():
            from PIL import Image
            baseline, changed = work/'baseline.png', work/'changed.png'
            Image.new('RGB',(32,32),'white').save(baseline)
            Image.new('RGB',(32,32),'black').save(changed)
            for label,capture,code,status in [('same',baseline,0,'passed'),('changed',changed,1,'failed')]:
                output=work/('pixels-'+label)
                p,value=cli('compare-visual','--baseline',baseline,'--harmony-capture',capture,
                            '--state-id','selftest','--output-dir',output)
                require(p.returncode==code,p.stdout+p.stderr)
                report=json.loads((output/'comparison.json').read_text(encoding='utf-8'))
                require(report['status']==status,'Visual comparison did not distinguish different pixels')
                require(value['quality_gate_granted'] is False,'Pixel comparison granted delivery')
        check('visual_pixels_positive_and_negative',visual_pixels)
        def visual_lifecycle():
            p=subprocess.run([sys.executable,str(root/'runtime/harness/examples/visual_demo.py'),
                '--output',str(work/'visual-demo')],capture_output=True,encoding='utf-8',timeout=30,cwd=work)
            require(p.returncode==0,p.stdout+p.stderr)
            result=json.loads(p.stdout)
            require(result['status']=='demo_only' and result['comparison_reports']==2,
                    'Built-in visual adapter did not produce bound reports')
        check('visual_bound_lifecycle',visual_lifecycle)
        def real_visual_binding():
            source=work/'reviewed-demo/contract.json'
            contract=json.loads(source.read_text(encoding='utf-8'))
            contract['demo']=False
            target=work/'unbound-real.json'
            write(target,contract)
            p,value=cli('start','--contract',target,'--run-dir',work/'unbound-real-run')
            error=json.loads(p.stderr) if p.stderr.strip() else value
            require(p.returncode==2 and 'Real visual tasks require visual_comparison' in error.get('error',''),
                    'Real visual task accepted without pixel binding: '+p.stdout+p.stderr)
            require(not (work/'unbound-real-run').exists(),'Rejected contract created a run')
        check('real_visual_binding_required',real_visual_binding)
        def locks():
            lock=work/'mutex'
            script=('import runpy,sys; from pathlib import Path; r=runpy.run_path(sys.argv[1]); '
                    'cm=r["file_lock"](Path(sys.argv[2]),shared=True,timeout=.1); '
                    'v=cm.__enter__(); print("acquired" if v else "busy",flush=True); cm.__exit__(None,None,None)')
            def attempt():
                return subprocess.check_output([sys.executable,'-c',script,str(core),str(lock)],encoding='utf-8',timeout=5).strip()
            with runtime['file_lock'](lock) as got:
                require(got,'Could not lock a new file');require(attempt()=='busy','Reader bypassed writer')
            require(attempt()=='acquired','Lock did not release')
        check('cross_process_lock_contention_and_release',locks)
        def python_adapter():
            script=work/'adapter.py';script.write_text('print("candidate")\n',encoding='utf-8')
            if os.name!='nt':
                script.write_text('#!'+sys.executable+'\n'+script.read_text(encoding='utf-8'),encoding='utf-8');script.chmod(0o755)
            result=runtime['run_process']([str(script)],cwd=work,log_path=work/'adapter.log',progress_path=work/'adapter.json',timeout_seconds=5,idle_timeout_seconds=5)
            require(result['exit_code']==0,'Python adapter failed')
        check('python_adapter_entry',python_adapter)
        def timeout_tree():
            marker=work/'leaked-child'
            child=f'import time; from pathlib import Path; time.sleep(2); Path({str(marker)!r}).write_text("leaked")'
            parent=f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); print("started",flush=True); time.sleep(15)'
            result=runtime['run_process']([sys.executable,'-u','-c',parent],cwd=work,
                 log_path=work/'timeout.log',progress_path=work/'timeout.json',timeout_seconds=.8,idle_timeout_seconds=10)
            time.sleep(2.2)
            require(result['status']=='timed_out' and result['exit_code']==124,'Timeout reported success')
            require(not marker.exists(),'Child survived cancellation')
        check('bounded_timeout_and_child_cleanup',timeout_tree)
        def lifecycle():
            run=work/'reviewed-demo/run'
            request=work/'session-request.json'
            write(request,{'action':'start','session_id':'selftest','request':'检查未完成的交付',
                           'scope':{'mode':'full'}})
            p,value=cli('session','--run-dir',run,'--operation-id','session','--input',request)
            require(p.returncode==0,p.stderr)
            stop=work/'stop.json'
            write(stop,{'session_id':'selftest','reason':'request_complete'})
            p,value=cli('end-check','--run-dir',run,'--operation-id','stop','--input',stop)
            require(p.returncode==1 and not value['allowed'],'Incomplete delivery allowed ending')
            before={p.relative_to(run):p.read_bytes() for p in run.rglob('*.json')}
            p,value=cli('resume','--run-dir',run)
            require(p.returncode==0,p.stderr)
            require(before=={p.relative_to(run):p.read_bytes() for p in run.rglob('*.json')},
                    'Read-only recovery changed records')
        check('session_end_check_and_read_only_recovery',lifecycle)
    return {'status':'passed' if all(c['status']=='passed' for c in checks) else 'failed',
        'platform':__import__('platform').platform(),'python':sys.version,'checks':checks,
        'scope':'offline synthetic portability; no real DevEco build, browser, device, business acceptance or optional HomeTrans validation',
        'quality_gate_granted':False}


if __name__=='__main__':
    chosen=python_path()
    if Path(chosen).absolute()!=Path(sys.executable).absolute():
        raise SystemExit(subprocess.call([chosen,str(Path(__file__).resolve()),*sys.argv[1:]]))
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output');a=p.parse_args()
    result=selftest()
    if a.output:write(Path(a.output),result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['status']=='passed' else 2)
