import shutil,subprocess,uuid
from pathlib import Path
from tools.bootstrap import configure
from harness import research as R,storage as S
from harness.util import git_env
ROOT=Path(__file__).resolve().parents[1]
def make_project(path,full=False):
    path=Path(path)
    if full:
        shutil.copytree(ROOT,path,ignore=shutil.ignore_patterns('.git','.beads','.cache','.venv','__pycache__','*.pyc','project.json','work','validation','local'))
    else:
        path.mkdir();shutil.copy2(ROOT/'harness.toml',path/'harness.toml');(path/'problem').mkdir();shutil.copy2(ROOT/'problem/contract.json',path/'problem/contract.json')
    configure(path,'fixture-game','fixture','',True)
    return path

def mission(root,limits=None,permissions=None,**extra):
    rec=R.input_record(root,'Use this isolated test project for bounded validation','directive')
    spec={'goal':'Test fixtures only','scope':['isolated test'],'stop_conditions':['test complete'],
          'permissions':permissions or {'evaluation':True,'training':True,'local_adopt':True},
          'limits':limits or {'solver_runs':1000,'training_runs':5},**extra}
    return R.mission_new(root,spec,rec['input_id'])

def git(root,*args):
    p=subprocess.run(['git','-c','user.name=Template Test','-c','user.email=test@example.invalid',*args],cwd=root,capture_output=True,text=True,env=git_env())
    if p.returncode:raise AssertionError(p.stderr)
    return p.stdout.strip()

def commit(root):git(root,'add','.');git(root,'commit','-qm','fixture')
