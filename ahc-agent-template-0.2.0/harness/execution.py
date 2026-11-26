"""File-backed child execution inside a single trial driver's process group.
The outer harness bounds/kills the driver group. This is NOT an OS sandbox.
"""
import os, subprocess, time
from pathlib import Path
from .process import clean_env

def run_files(argv, *, cwd, stdin_path, stdout_path, stderr_path, timeout,
              max_output, max_stderr, env=None):
    start=time.monotonic();status='ok';code=None
    source=open(stdin_path,'rb') if stdin_path else open(os.devnull,'rb')
    with source, open(stdout_path,'wb') as out, open(stderr_path,'wb') as err:
        try:
            p=subprocess.Popen(argv,cwd=cwd,stdin=source,stdout=out,stderr=err,
                               env=clean_env(env),start_new_session=False)
        except OSError as e:
            err.write(str(e).encode());return {'status':'spawn_error','returncode':None,'wall_s':time.monotonic()-start}
        try:
            while True:
                code=p.poll()
                if Path(stdout_path).stat().st_size>max_output or Path(stderr_path).stat().st_size>max_stderr:
                    status='output_limit';break
                if code is not None:
                    status='ok' if code==0 else 'crash';break
                if time.monotonic()-start>=timeout: status='timeout';break
                time.sleep(.005)
        finally:
            if p.poll() is None:p.kill()
            code=p.wait()
    # File limits are sampled, so the disk may temporarily exceed the cap. Truncate retained bytes.
    for path,cap in ((stdout_path,max_output),(stderr_path,max_stderr)):
        if Path(path).stat().st_size>cap:
            status='output_limit'
            with open(path,'r+b') as f:f.truncate(cap)
    return {'status':status,'returncode':code,'wall_s':time.monotonic()-start,
            'memory_peak_bytes':None,'cpu_s':None}
