#!/usr/bin/env python3
"""Optional real-bd checks in a disposable repository and disposable HOME. No external remote."""
import json,os,shutil,subprocess,tempfile,sys
from pathlib import Path

def main():
    executable=shutil.which('bd')
    if not executable:print(json.dumps({'ok':False,'reason':'bd not installed; live acceptance NOT performed'}));return 2
    with tempfile.TemporaryDirectory(prefix='ahc-bd-test-') as td:
        base=Path(td);w=base/'main';w.mkdir();home=base/'home';home.mkdir();env={**os.environ,'HOME':str(home),'XDG_CONFIG_HOME':str(home/'.config'),'BD_NON_INTERACTIVE':'1'}
        env.pop('BEADS_DIR',None);env.pop('GITHUB_TOKEN',None)
        def run(cwd,*args):
            p=subprocess.run(list(args),cwd=cwd,env=env,capture_output=True,text=True,timeout=120)
            if p.returncode:raise RuntimeError(p.stderr[:500])
            return p.stdout.strip()
        def bd(*args,cwd=w):return run(cwd,executable,*args)
        def git(*args):return run(w,'git',*args)
        git('init','-q','-b','main');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
        (w/'AGENTS.md').write_text('project-specific rules\n');(w/'CLAUDE.md').write_text('@AGENTS.md\n');git('add','.');git('commit','-qm','fixture')
        bd('metrics','off');version=bd('version')
        bd('--sandbox','init','--quiet','--skip-agents','--skip-hooks','--non-interactive','-p','test')
        bd('--sandbox','config','set','dolt.local-only','true')
        checks={'entry_preserved':(w/'AGENTS.md').read_text()=='project-specific rules\n','local_only':'true' in bd('config','get','dolt.local-only')}
        a=bd('q','candidate task','-t','task');b=bd('q','idea','-t','task','-l','proposal')
        bd('defer',b,'--reason','not an adopted plan');ready=json.loads(bd('ready','--exclude-label','proposal','--json'))
        checks['proposal_not_ready']=b not in [x['id'] for x in ready]
        bd('update',a,'--claim');started=json.loads(bd('list','--status','in_progress','--json'));checks['progress_persists']=a in [x['id'] for x in started]
        git('add','.');git('commit','-qm','tracker config')
        child=base/'child';git('worktree','add','-q','-b','child',str(child))
        via=json.loads(bd('show',a,'--json',cwd=child));checks['same_db_in_worktree']=bool(via and via[0]['id']==a)
        result={'bd_version':version,'checks':checks,'ok':all(checks.values()),'external_network_used':False}
        print(json.dumps(result,indent=2));return 0 if result['ok'] else 1
if __name__=='__main__':
    try:raise SystemExit(main())
    except (RuntimeError,OSError,ValueError,subprocess.TimeoutExpired) as e:print(json.dumps({'ok':False,'error':str(e)}));raise SystemExit(2)
