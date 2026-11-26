from __future__ import annotations
import hashlib, json, os, re, sys, tempfile, time
from pathlib import Path

class ThinError(Exception):pass

def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for part in iter(lambda:f.read(1<<20),b''):h.update(part)
    return h.hexdigest()

def write_json(path,object):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,prefix='.tmp-',delete=False) as f:
        tmp=Path(f.name);json.dump(object,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)

def read_json(path):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError) as e:raise ThinError(f'Invalid or missing JSON: {path}: {e}') from e

def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

def safe_id(s):
    if not isinstance(s,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,80}',s):raise ThinError('Invalid ID: '+str(s))
    return s

def relpath(root,ref):
    p=(Path(root)/ref).resolve();r=Path(root).resolve()
    if not p.is_relative_to(r):raise ThinError('Unsafe path outside project: '+str(ref))
    return p

def format_argv(argv,mapping):
    if not isinstance(argv,list) or not argv or any(not isinstance(x,str) for x in argv):
        raise ThinError('Command must be a nonempty JSON array of strings; shell syntax is not supported')
    try:return [x.format_map(mapping) for x in argv]
    except (ValueError,KeyError,IndexError) as e:raise ThinError(f'Invalid command placeholder: {e}') from e

def root_from(start=None):
    p=Path(start or os.getcwd()).resolve()
    for base in [p,*p.parents]:
        if (base/'config.json').is_file() and (base/'thin'/'__init__.py').exists():return base
    raise ThinError('Open the project root containing config.json and thin/ or use --root')
