#!/usr/bin/env python3
"""Allowlisted review packet. No automatic read of private notes, credentials, raw API responses or bd export."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness import storage as S,research as R
from harness.config import root as find_root,project
from harness.util import HarnessError,save,now
from integrations.beads.bridge import issue

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--bead',action='append',default=[]);a=p.parse_args();r=find_root()
    out=Path(a.out).resolve()
    if out.exists():raise HarnessError('Review destination exists; choose a new packet directory')
    s=S.read(r);m=s['payload']['mission'];pr=project(r)
    packet={'at':now(),'head':S.git(r,'rev-parse','HEAD'),'template_version':pr['template_version'],'project_name':pr['name'],
      'problem':pr['problem_id'],'state_generation':s['generation'],
      'mission':{k:m.get(k) for k in ('id','goal','status','limits','scope')} if m else None,'budget':R.budget(s['payload']),
      'shared_refs':{k:v['artifact_id'] for k,v in s['payload']['refs'].items() if not k.startswith('dev:')},
      'beads':[{k:v for k,v in issue(r,b).items() if k in ('id','title','status','priority','description','labels','external_ref')} for b in a.bead],
      'unavailable':['raw runs','private human notes','full datasets/models','authentication','unsaved agent thoughts']}
    text=json.dumps(packet,ensure_ascii=False,indent=2)
    from integrations.beads.github_issues import SENSITIVE
    if SENSITIVE.search(text):raise HarnessError('Selected content may contain private information. Review before export.')
    out.mkdir(parents=True);save(out/'manifest.json',packet);(out/'context.md').write_text('# Review context\n\n```json\n'+text+'\n```\n',encoding='utf-8')
    print(json.dumps({'out':str(out),'network_used':False,'private_notes_read':False}))
if __name__=='__main__':main()
