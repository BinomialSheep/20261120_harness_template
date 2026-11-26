#!/usr/bin/env python3
"""Explicit selective mirror using native bd github push; never bidirectional sync/pull."""
from __future__ import annotations
import argparse,json,os,re,subprocess,sys,urllib.request,urllib.error,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from harness.config import root as find_root,project
from harness import storage as S,research as R
from harness.util import HarnessError,load,save,lock,now,digest
from integrations.beads.bridge import issue,environment,call
from integrations.no_redirect import NoRedirect

SENSITIVE=re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|gh[pousr]_[A-Za-z0-9]{15,}|github_pat_|BEGIN .*PRIVATE KEY|/home/[^/]+/|[A-Za-z]:\\Users\\')
def target(root):
    value=project(root).get('github_repository','')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',value):raise HarnessError('Configure the exact GitHub repository in project.json')
    return value

def api(root,path):
    token=os.environ.get('GITHUB_TOKEN')
    if not token:raise HarnessError('Supply GITHUB_TOKEN locally, never in chat or project files')
    base='/repos/'+target(root)
    if not (path==base or path.startswith(base+'/') or path.startswith(base+'?')):raise HarnessError('API read outside configured repository')
    req=urllib.request.Request('https://api.github.com'+path,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'ahc-harness'})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=30) as r:return json.loads(r.read(8*1024*1024))

def inspect_issue(root,number):
    if not project(root)['access'].get('issues_read'):raise HarnessError('Issue reads disabled')
    base='/repos/'+target(root)+'/issues/'+str(int(number));body=api(root,base);comments=[];page=1
    while True:
        rows=api(root,base+f'/comments?per_page=100&page={page}');comments.extend(rows)
        if len(rows)<100:break
        page+=1
    # Remote body/comments are not executed or applied to Beads, mission or task priority.
    return {'authority':'advisory','issue':body,'comments':comments,'tasks_modified':False}

def preview(root,bid):
    b=issue(root,bid);labels=b.get('labels') or []
    if 'share:github' not in labels or 'proposal' in labels or 'origin:external' in labels:raise HarnessError('Only explicitly selected, adopted local issues can be mirrored')
    text=b.get('title','')+'\n'+b.get('description','')
    if SENSITIVE.search(text):raise HarnessError('Potential sensitive content; sanitize before publication')
    if len(text)>20000:raise HarnessError('Use links to evidence rather than copying large logs')
    repo=target(root);ref=b.get('external_ref') or ''
    if ref and not re.fullmatch(r'https://github\.com/'+re.escape(repo)+r'/issues/\d+',ref):raise HarnessError('external_ref points elsewhere')
    return {'bead_id':bid,'repository':repo,'title':b['title'],'body':b.get('description',''),'labels':labels,'external_ref':ref,'action':'update' if ref else 'create','sha256':digest(text)}

def push(root,bid,work,confirm):
    if confirm!=bid:raise HarnessError('Confirm the exact bead ID')
    item=preview(root,bid);S.require_integrator(root,'issue mirror')
    repo=target(root);owner,name=repo.split('/')
    remote=api(root,'/repos/'+repo)
    if (remote.get('full_name') or '').lower()!=repo.lower() or not remote.get('has_issues'):raise HarnessError('Repository mismatch/issues disabled')
    # Project visibility is checked at each real publication; publishing to public needs a separately confirmed policy.
    if not remote.get('private',False) and not project(root).get('allow_public_issues',False):raise HarnessError('Public publication was not explicitly enabled')
    for key,want in (('github.owner',owner),('github.repo',name),('github.repository',repo),('github.url','https://api.github.com')):
        q=subprocess.run(['bd','config','get',key],cwd=root,env=environment(root),capture_output=True,text=True,timeout=30)
        if q.returncode and not any(x in (q.stderr+q.stdout).lower() for x in ('not found','not set','no value')):raise HarnessError('Could not validate native bd target')
        val=q.stdout.strip() if not q.returncode else ''
        if val and val not in ('null','<not set>','(not set)') and val!=want:raise HarnessError('Existing bd target conflicts: '+key)
    with lock(S.base(root)/'locks/issues.lock'):
        recs=S.evidence(root,'receipts')
        if any(load(f).get('kind')=='github_issue' and load(f).get('outcome')=='pending_confirmation' for f in recs.glob('*.json')):
            raise HarnessError('Unresolved issue write; reconcile the remote before retrying')
        with R.operation(root,'issues_write',{'issue_writes':1},work,target=bid):
            rec={'kind':'github_issue','outcome':'pending_confirmation','preview':item,'at':now()};path=recs/('github-'+uuid.uuid4().hex+'.json');save(path,rec)
            env=environment(root);env.update(GITHUB_OWNER=owner,GITHUB_REPO=name,GITHUB_REPOSITORY=repo);env.pop('GITHUB_API_URL',None)
            p=subprocess.run(['bd','github','push',bid],cwd=root,env=env,capture_output=True,text=True,timeout=120)
            if p.returncode:raise HarnessError('Issue write failed/unknown; retained pending receipt. Inspect remotely, do not blindly resend.')
            after=issue(root,bid);url=after.get('external_ref','')
            if not re.fullmatch(r'https://github\.com/'+re.escape(repo)+r'/issues/\d+',url):raise HarnessError('Issue URL missing; retained pending receipt')
            rec.update(outcome='complete',external_ref=url);save(path,rec)
    return {'receipt':str(path),'url':url}

def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    a=s.add_parser('preview');a.add_argument('id')
    a=s.add_parser('read');a.add_argument('number',type=int)
    a=s.add_parser('push');a.add_argument('id');a.add_argument('--work',required=True);a.add_argument('--confirm',required=True)
    a=p.parse_args();r=find_root()
    out=preview(r,a.id) if a.cmd=='preview' else inspect_issue(r,a.number) if a.cmd=='read' else push(r,a.id,a.work,a.confirm)
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':
    try:main()
    except (HarnessError,OSError,ValueError,KeyError,subprocess.SubprocessError) as e:print(json.dumps({'error':str(e)}),file=sys.stderr);raise SystemExit(2)
