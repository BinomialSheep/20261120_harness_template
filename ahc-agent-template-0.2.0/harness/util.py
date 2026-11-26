from __future__ import annotations
import fcntl, hashlib, json, math, os, re, tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

class HarnessError(RuntimeError): pass

def now(): return datetime.now(timezone.utc).isoformat()
def canonical(x): return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
def digest(x): return hashlib.sha256(canonical(x)).hexdigest()
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()
def load(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def save(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f: f.write(canonical(value)+b'\n'); f.flush(); os.fsync(f.fileno())
        os.replace(name,path)
        dfd=os.open(path.parent,os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(name): os.unlink(name)
def finite(x): return type(x) in (int,float) and math.isfinite(x)
def nonnegative(x): return finite(x) and x>=0

def safe_name(x):
    if not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',x) or x in ('.','..'):
        raise HarnessError(f'Unsafe identifier: {x!r}')
    return x

def contained(root, path, exists=True):
    root=Path(root).resolve(); p=Path(path)
    if not p.is_absolute(): p=root/p
    p=p.resolve()
    if not p.is_relative_to(root): raise HarnessError('Path escapes its root')
    if exists and not p.exists(): raise HarnessError(f'Missing path: {p}')
    return p

@contextmanager
def lock(path, blocking=True):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a') as f:
        try: fcntl.flock(f,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as e: raise HarnessError('Another operation holds this lock') from e
        try: yield
        finally: fcntl.flock(f,fcntl.LOCK_UN)


def git_env():
    # Local repository operations must never follow another agent's GIT_DIR/index.
    return {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
