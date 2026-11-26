#!/usr/bin/env python3
"""Idempotent, local setup after the user copies the template. No network or rule inference."""
import argparse, json, re, subprocess, sys, uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness import storage
from harness.util import HarnessError,load,save,git_env

def configure(root,problem,name,github='',init_git=False):
    root=Path(root).resolve()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',problem):
        raise HarnessError('Problem ID must be a local identifier, e.g. ahcXXX_a or practice-routing')
    if not name.strip(): raise HarnessError('Project name required')
    if github and not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',github): raise HarnessError('GitHub is owner/repository')
    if not storage.git(root,'rev-parse','--git-common-dir'):
        if not init_git: raise HarnessError('Use --init-git for a fresh copied directory')
        r=subprocess.run(['git','init','-b','main',str(root)],capture_output=True,text=True,env=git_env())
        if r.returncode: raise HarnessError(r.stderr)
    if Path(storage.git(root,'rev-parse','--show-toplevel')).resolve()!=root: raise HarnessError('Use the checkout root, not a subdirectory of another repository')
    path=root/'project.json'
    if path.exists():
        old=load(path)
        if old.get('problem_id')!=problem or old['name']!=name or old.get('github_repository','')!=github:
            raise HarnessError('Existing project differs; do not silently retarget')
    else:
        save(path,{'schema_version':1,'project_id':uuid.uuid4().hex,'name':name,'problem_id':problem,
            'github_repository':github,'access':{'issues_read':True},'template_version':'0.2.0'})
    state=storage.init(root)
    return {'problem':problem,'store_id':state['store_id'],'network_calls':0,
            'next':'docs/BOOTSTRAP_AGENT.md. Supplied rules are optional project material, never inferred from contest/date/division.'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--problem',required=True);p.add_argument('--name',required=True)
    p.add_argument('--github',default='');p.add_argument('--init-git',action='store_true');a=p.parse_args()
    print(json.dumps(configure(ROOT,a.problem,a.name,a.github,a.init_git),ensure_ascii=False,indent=2))
if __name__=='__main__':
    try: main()
    except (HarnessError,OSError,ValueError,KeyError) as e: print(str(e),file=sys.stderr);raise SystemExit(2)
