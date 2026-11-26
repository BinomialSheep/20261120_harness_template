"""Advisory user-wide machine coordination, not CPU/GPU quotas or an OS sandbox."""
from __future__ import annotations
import fcntl, json, os, time
from contextlib import contextmanager
from pathlib import Path
from .util import HarnessError, now

def path(): return Path(os.environ.get('ARENA_MACHINE_LOCK',str(Path.home()/'.cache/codingame-harness/machine.lock')))

@contextmanager
def reserve(mode,label,wait=False):
    if mode not in ('shared','exclusive'): raise HarnessError('Unknown machine reservation')
    p=path(); p.parent.mkdir(parents=True,exist_ok=True); begin=time.monotonic()
    with p.open('a') as f:
        flag=fcntl.LOCK_SH if mode=='shared' else fcntl.LOCK_EX
        try: fcntl.flock(f,flag | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as e: raise HarnessError('Machine busy; defer the timing test, do not stop another task') from e
        record=p.parent/f'holder-{os.getpid()}-{time.monotonic_ns()}.json'
        record.write_text(json.dumps({'pid':os.getpid(),'mode':mode,'label':label,'at':now()}))
        try: yield {'mode':mode,'waited_s':time.monotonic()-begin,'load':os.getloadavg()}
        finally: record.unlink(missing_ok=True); fcntl.flock(f,fcntl.LOCK_UN)
