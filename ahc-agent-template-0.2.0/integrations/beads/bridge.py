"""Use one Beads workspace for all linked code worktrees. No tracker reimplementation."""
import json,os,subprocess
from pathlib import Path
from harness import storage as S
from harness.util import HarnessError

def environment(root):
    home=Path(S.read(root)['payload']['integration_root'])/'.beads'
    return {**os.environ,'BEADS_DIR':str(home),'BD_NON_INTERACTIVE':'1'}

def call(root,args,timeout=90):
    p=subprocess.run(['bd',*args],cwd=root,env=environment(root),capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise HarnessError('Beads command failed (inspect locally): '+p.stderr[:500])
    return p.stdout

def query(root,args):
    out=call(root,[*args,'--json']);value=json.loads(out or 'null')
    return value or []

def issue(root,identifier):
    d=query(root,['show',identifier]);return d[0] if isinstance(d,list) else d
