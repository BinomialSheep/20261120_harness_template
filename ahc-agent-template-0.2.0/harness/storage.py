"""One checksummed state generation per local Git common-dir. No automatic snapshot restore."""
from __future__ import annotations
import copy, os, shutil, subprocess, uuid
from contextlib import contextmanager
from pathlib import Path
from .config import project
from .util import HarnessError, digest, load, lock, now, save, git_env

def git(root,*args):
    r=subprocess.run(['git',*args],cwd=root,capture_output=True,text=True,env=git_env())
    return r.stdout.rstrip() if r.returncode==0 else None

def base(root):
    common=git(root,'rev-parse','--path-format=absolute','--git-common-dir')
    if not common: raise HarnessError('Initialize a local Git repository first')
    return Path(common).resolve()/'ahc-harness'

def state_path(root): return base(root)/'state.json'

def validate(d, pid=None):
    if not isinstance(d,dict) or d.get('schema_version')!=1 or d.get('sha256')!=digest(d.get('payload')):
        raise HarnessError('Shared state corrupted: no fallback to a Git snapshot')
    if not isinstance(d.get('generation'),int) or not d.get('store_id'): raise HarnessError('Invalid state identity')
    p=d['payload']
    if pid is not None and p.get('project_id')!=pid: raise HarnessError('Project identity differs from shared state')
    for k in ('integration_root','mission','missions','events','reservations','refs','outputs'):
        if k not in p: raise HarnessError('Incomplete shared state: '+k)
    return d

def read(root):
    try: return validate(load(state_path(root)),project(root)['project_id'])
    except (OSError,ValueError,KeyError) as e: raise HarnessError('Shared state missing or broken; initialize/restore explicitly, never invent a fresh budget') from e

def init(root):
    root=Path(root).resolve(); b=base(root); conf=project(root)
    with lock(b/'state.lock'):
        if (b/'state.json').exists(): return read(root)
        if (root/'.git').is_file(): raise HarnessError('Initialize in the primary checkout, not a linked worktree')
        if (b/'initialized.json').exists(): raise HarnessError('Previously initialized store is missing; explicit recovery required')
        p={'project_id':conf['project_id'],'integration_root':str(root),'mission':None,'missions':[],
           'events':[],'reservations':{},'refs':{},'outputs':{},'observations':[]}
        d={'schema_version':1,'store_id':uuid.uuid4().hex,'generation':0,'updated_at':now(),'payload':p,'sha256':digest(p)}
        save(b/'initialized.json',{'project_id':conf['project_id'],'store_id':d['store_id']}); save(b/'state.json',d)
        return d

@contextmanager
def transaction(root, expected_generation=None):
    with lock(base(root)/'state.lock'):
        s=read(root)
        if expected_generation is not None and s['generation']!=expected_generation: raise HarnessError('Shared state changed; refresh before modifying')
        p=copy.deepcopy(s['payload']); yield p
        if p!=s['payload']:
            save(state_path(root),{**s,'generation':s['generation']+1,'updated_at':now(),'payload':p,'sha256':digest(p)})

def event(p,kind,**kw):
    e={'id':f'event-{len(p["events"])+1:07d}','at':now(),'kind':kind,**kw}; p['events'].append(e); return e

def integrator(root): return Path(root).resolve()==Path(read(root)['payload']['integration_root']).resolve()
def require_integrator(root,op):
    if not integrator(root): raise HarnessError(op+' is restricted to the integration checkout; workers can continue local work')

def evidence(root,kind):
    if kind not in ('objects','runs','datasets','models','casesets','receipts','imports','sealed','experiments'):
        raise HarnessError('Unknown evidence namespace')
    p=base(root)/'evidence'/kind; p.mkdir(parents=True,exist_ok=True); return p

def backup(root,out):
    out=Path(out).resolve()
    if out.exists(): raise HarnessError('Backup already exists')
    with lock(base(root)/'state.lock'): save(out,read(root))
    return {'path':str(out),'includes':'research state only; evidence and Beads require their own backup'}

def restore(root,source,store_id):
    b=base(root); src=validate(load(Path(source)),project(root)['project_id'])
    if src['store_id']!=store_id: raise HarnessError('Backup store ID mismatch')
    with lock(b/'state.lock'):
        try: read(root)
        except HarnessError: pass
        else: raise HarnessError('Refuse to roll back a healthy state store')
        if (b/'state.json').exists(): shutil.copy2(b/'state.json',b/f'damaged-{uuid.uuid4().hex}.json')
        p=src['payload']; m=p['mission']
        if m: m['status']='paused'; m['reason']='Recovered backup: reconcile processes, receipts and later usage before resuming'
        event(p,'restored',warning='Post-backup usage may be missing; reconcile before resume')
        out={**src,'generation':src['generation']+1,'payload':p,'sha256':digest(p),'updated_at':now()}
        save(b/'state.json',out); save(b/'initialized.json',{'project_id':p['project_id'],'store_id':store_id})
    return {'restored':True,'status':'paused','generation':out['generation']}
