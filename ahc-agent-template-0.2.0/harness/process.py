"""Bounded subprocess capture. Linux/WSL process groups; NOT a security sandbox."""
from __future__ import annotations
import os, signal, subprocess, threading, time
from pathlib import Path
from .util import HarnessError

# Never inherit API keys or arbitrary tuning variables into evaluated programs.
ENV_KEYS=('PATH','HOME','LANG','LC_ALL','TMPDIR','SYSTEMROOT')
def clean_env(extra: dict | None=None) -> dict:
    env={k:os.environ[k] for k in ENV_KEYS if k in os.environ}
    env.update({'LC_ALL':'C.UTF-8','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1',
                'MKL_NUM_THREADS':'1','PYTHONHASHSEED':'0'})
    for k,v in (extra or {}).items():
        if any(s in k.upper() for s in ('TOKEN','SECRET','PASSWORD','API_KEY')):
            raise HarnessError(f'Secrets cannot be passed to evaluated processes: {k}')
        env[str(k)]=str(v)
    return env

def kill_group(p: subprocess.Popen) -> None:
    try: os.killpg(p.pid,signal.SIGKILL)
    except ProcessLookupError: pass

def run(argv: list[str], *, cwd: Path, timeout: float, env=None,
        max_output: int=1_048_576, stdin: bytes | None=None, on_start=None) -> dict:
    if not argv or timeout<=0 or max_output<1: raise HarnessError('Invalid process limits/command')
    start=time.monotonic()
    try:
        p=subprocess.Popen(argv,cwd=cwd,env=clean_env(env),stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    except OSError as e:
        return dict(status='spawn_error',returncode=None,stdout='',stderr=str(e),wall_s=time.monotonic()-start)
    if on_start is not None:
        try: on_start(p.pid)
        except BaseException:
            kill_group(p); p.wait(); raise
    buffers=[bytearray(),bytearray()]; overflow=threading.Event()
    def reader(stream, idx):
        try:
            while True:
                b=stream.read(8192)
                if not b: break
                room=max_output-len(buffers[idx])
                buffers[idx].extend(b[:max(0,room)])
                if len(b)>room:
                    overflow.set();kill_group(p);break
        finally: stream.close()
    threads=[threading.Thread(target=reader,args=(p.stdout,0),daemon=True),
             threading.Thread(target=reader,args=(p.stderr,1),daemon=True)]
    for t in threads:t.start()
    def writer():
        try:
            p.stdin.write(stdin);p.stdin.flush()
        except (BrokenPipeError,OSError):pass
        finally:p.stdin.close()
    if stdin is not None:threading.Thread(target=writer,daemon=True).start()
    timed_out=False
    try:p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out=True;kill_group(p);p.wait()
    finally:
        # A driver must not leave children retaining stdout pipes after it exits.
        kill_group(p)
        for t in threads:t.join(timeout=2)
    status='output_limit' if overflow.is_set() else 'timeout' if timed_out else 'ok' if p.returncode==0 else 'crash'
    return dict(status=status,returncode=p.returncode,stdout=buffers[0].decode('utf-8','replace'),
                stderr=buffers[1].decode('utf-8','replace'),wall_s=time.monotonic()-start)
