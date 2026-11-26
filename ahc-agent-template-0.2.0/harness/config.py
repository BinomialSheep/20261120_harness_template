from __future__ import annotations
import tomllib
from pathlib import Path
from .util import HarnessError, load

def root(path=None):
    p=Path(path or Path.cwd()).resolve()
    for d in (p,*p.parents):
        if (d/'harness.toml').is_file(): return d
    raise HarnessError('Open the copied template directory (harness.toml not found)')

def read(path):
    return tomllib.loads((Path(path)/'harness.toml').read_text(encoding='utf-8'))

def project(path):
    p=Path(path)/'project.json'
    if not p.is_file(): raise HarnessError('Project unconfigured: follow docs/BOOTSTRAP_AGENT.md')
    d=load(p)
    if d.get('schema_version')!=1 or not d.get('project_id'): raise HarnessError('Invalid project.json')
    return d
