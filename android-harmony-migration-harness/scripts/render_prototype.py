#!/usr/bin/env python3
"""Capture local HTML with a dedicated Chrome profile; output is prototype evidence only."""
import argparse,json,shutil,subprocess,tempfile,sys,os,signal,time
from html import escape
from common import python_path,runtime_api
from pathlib import Path

def chrome_path():
    for key in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA'):
        base=os.environ.get(key)
        if base:
            for relative in ('Google/Chrome/Application/chrome.exe','Microsoft/Edge/Application/msedge.exe'):
                candidate=Path(base)/relative
                if candidate.is_file():return str(candidate)
    for p in ('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
              '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'):
        if Path(p).is_file():return p
    return next((shutil.which(n) for n in ('google-chrome','chromium','chromium-browser','chrome','msedge') if shutil.which(n)),None)

def render(html,output,width,height,browser=None):
    html=Path(html).expanduser().resolve(strict=True);output=Path(output).expanduser().absolute()
    if not html.is_file() or not (64<=width<=5000 and 64<=height<=5000):raise ValueError('Supply an HTML file and viewport dimensions between 64 and 5000')
    if output.exists() or output.is_symlink():raise ValueError('Output must be new; preserve earlier evidence')
    executable=browser or chrome_path()
    if not executable:raise ValueError('Install Chrome/Edge, or pass --browser with a Chromium executable')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='harness-prototype-browser-') as profile:
        # Chromium can enforce a minimum native window width. An exact-size frame
        # keeps the document viewport correct even when that window is wider.
        viewport=Path(profile)/'viewport.html'
        viewport.write_text('<!doctype html><meta charset="utf-8"><style>'
                            'html,body{margin:0;padding:0;overflow:hidden}iframe{display:block;border:0}'
                            '</style><iframe width="'+str(width)+'" height="'+str(height)+'" src="'
                            +escape(html.as_uri(),quote=True)+'"></iframe>',encoding='utf-8')
        argv=[executable,'--headless','--no-first-run','--no-default-browser-check',
              '--user-data-dir='+profile,'--hide-scrollbars','--force-device-scale-factor=1',
              '--window-size='+str(width)+','+str(height),'--virtual-time-budget=4000',
              '--screenshot='+str(output),viewport.as_uri()]
        log=Path(profile)/'browser.log'
        complete=False
        with log.open('w+') as stream:
            runtime=runtime_api()
            process=subprocess.Popen(runtime['executable_argv'](argv),stdout=stream,stderr=subprocess.STDOUT,**runtime['process_options']())
            deadline=time.monotonic()+45
            try:
                while time.monotonic()<deadline:
                    if output.is_file():
                        try:
                            from PIL import Image
                            with Image.open(output) as frame:
                                frame.load()
                                complete=frame.format=='PNG' and frame.size==(width,height)
                        except (OSError,ValueError):pass
                    if process.poll() is not None:break
                    time.sleep(.1)
            finally:
                if process.poll() is None:
                    runtime['_stop_process'](process)
                else:
                    process.wait()
            if not complete or process.returncode != 0:
                stream.seek(0);raise ValueError('Prototype rendering produced no complete viewport PNG: '+stream.read()[-1500:])
    return {'status':'rendered','output':str(output),'viewport':[width,height],
            'evidence_kind':'prototype_render','quality_gate_granted':False}

if __name__=='__main__':
    chosen=python_path()
    if Path(chosen).absolute()!=Path(sys.executable).absolute():raise SystemExit(subprocess.call([chosen,str(Path(__file__).resolve()),*sys.argv[1:]]))
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--html',required=True);p.add_argument('--output',required=True)
    p.add_argument('--width',type=int,required=True);p.add_argument('--height',type=int,required=True);p.add_argument('--browser');a=p.parse_args()
    try:print(json.dumps(render(a.html,a.output,a.width,a.height,a.browser),ensure_ascii=False,indent=2))
    except (ValueError,OSError,subprocess.SubprocessError) as exc:print(str(exc),file=sys.stderr);raise SystemExit(2)
