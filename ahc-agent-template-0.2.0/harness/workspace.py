"""Code in separate worktrees; authority, budgets and immutable evidence in one clone-local store."""
from pathlib import Path
import subprocess
from . import storage as S
from .util import HarnessError, digest, safe_name, git_env

def info(root):
    s=S.read(root)
    return {'checkout':str(Path(root).resolve()),'common_dir':str(S.base(root).parent),'store_id':s['store_id'],'generation':s['generation'],
            'integrator':S.integrator(root),'integration_root':s['payload']['integration_root'],
            'head':S.git(root,'rev-parse','HEAD'),'workspace_id':digest(str(Path(root).resolve()))[:12]}

def create(root,path,branch):
    safe_name(branch); dest=Path(path).expanduser().resolve()
    if dest.exists(): raise HarnessError('Worktree path already exists')
    if S.git(root,'status','--porcelain'): raise HarnessError('Commit/reconcile changes before creating a worktree; never auto-stash the user\'s work')
    r=subprocess.run(['git','worktree','add','-b',branch,str(dest),'HEAD'],cwd=root,capture_output=True,text=True,env=git_env())
    if r.returncode: raise HarnessError(r.stderr)
    if S.read(dest)['store_id']!=S.read(root)['store_id']: raise HarnessError('Unexpected shared store')
    return {'path':str(dest),'branch':branch,'next':'Open this folder in a separate VS Code window; no tmux required'}

def check_remove(root):
    root=Path(root).resolve(); s=S.read(root); p=s['payload']; reasons=[]
    if S.integrator(root): reasons.append('Integration checkout is not removable through this workflow')
    if S.git(root,'status','--porcelain'): reasons.append('Uncommitted/untracked changes')
    head=S.git(root,'rev-parse','HEAD'); other=S.git(p['integration_root'],'rev-parse','HEAD')
    if head and other:
        r=subprocess.run(['git','merge-base','--is-ancestor',head,other],cwd=root,capture_output=True,env=git_env())
        if r.returncode: reasons.append('Worker commits have not been integrated')
    if any(r['status']=='held' and r.get('workspace')==str(root) for r in p['reservations'].values()): reasons.append('Unsettled work reservations')
    for folder in ('work','human/local'):
        local=root/folder
        for f in local.rglob('*') if local.exists() else []:
            if not f.is_file(): continue
            rel=str(f.relative_to(root)); covered=False
            from .util import sha
            for o in p['outputs'].values():
                prefix=o['source_path'].rstrip('/')+'/'
                if o['source_workspace']==str(root) and rel.startswith(prefix) and o['files'].get(rel[len(prefix):])==sha(f): covered=True;break
            if not covered: reasons.append('Unsealed local output or private note: '+rel); break
    return {'can_remove':not reasons,'reasons':reasons,'deleted':False,'evidence':'shared immutable objects/runs survive worker removal'}
