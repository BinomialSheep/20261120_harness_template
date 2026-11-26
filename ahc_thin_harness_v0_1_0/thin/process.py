"""Subprocess-with-timeout and process-group cleanup. Not a security sandbox."""
from __future__ import annotations
import os,signal,subprocess,time
from pathlib import Path
from .util import ThinError,now

def execute(argv,cwd,seconds,stdin=None,stdout_file=None,stderr_file=None,env=None):
    if seconds<=0:raise ThinError('Timeout must be positive')
    cwd=Path(cwd)
    outpath=Path(stdout_file) if stdout_file else cwd/'_stdout.txt'
    errpath=Path(stderr_file) if stderr_file else cwd/'_stderr.txt'
    outpath.parent.mkdir(parents=True,exist_ok=True);errpath.parent.mkdir(parents=True,exist_ok=True)
    t=time.monotonic()
    try:
        with open(outpath,'wb') as o,open(errpath,'wb') as e:
            p=subprocess.Popen(argv,cwd=cwd,stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                               stdout=o,stderr=e,start_new_session=True,env=env)
            try:p.communicate(input=stdin.encode('utf-8') if isinstance(stdin,str) else stdin,timeout=seconds);timed_out=False
            except subprocess.TimeoutExpired:
                timed_out=True
                try:os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                p.communicate()
            except BaseException:
                try:os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                p.communicate()
                raise
        return {'status':'timeout' if timed_out else ('ok' if p.returncode==0 else 'error'),
                'returncode':p.returncode,'seconds':round(time.monotonic()-t,3),
                'stdout':str(outpath),'stderr':str(errpath)}
    except OSError as ex:
        return {'status':'launch_error','error':str(ex),'seconds':round(time.monotonic()-t,3),
                'stdout':str(outpath),'stderr':str(errpath)}
