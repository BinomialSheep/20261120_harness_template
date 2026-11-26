#!/usr/bin/env python3
"""Search controller: python3 -m integrations.search.cli ..."""
import argparse,json,sys
from pathlib import Path
from harness.config import root as find_root
from harness.util import HarnessError,load
from . import controller as C

def main():
    p=argparse.ArgumentParser(description='Optional AHC candidate-branch selector');sp=p.add_subparsers(dest='cmd',required=True)
    a=sp.add_parser('init');a.add_argument('--plan',required=True)
    for cmd in ('ask','status','verify'):
        a=sp.add_parser(cmd);a.add_argument('search_id')
    a=sp.add_parser('task');a.add_argument('search_id');a.add_argument('trial_id')
    for cmd in ('start','fail','abort'):
        a=sp.add_parser(cmd);a.add_argument('search_id');a.add_argument('trial_id');a.add_argument('--reason',default='')
    a=sp.add_parser('tell');a.add_argument('search_id');a.add_argument('trial_id');a.add_argument('--artifact',required=True);a.add_argument('--run',required=True)
    a=sp.add_parser('render');a.add_argument('search_id');a.add_argument('--out',required=True)
    a=p.parse_args();root=find_root()
    try:
        if a.cmd=='init':result=C.init(root,load(a.plan))
        elif a.cmd=='ask':result=C.ask(root,a.search_id)
        elif a.cmd=='task':result=C.get_task(root,a.search_id,a.trial_id)
        elif a.cmd=='status':result=C.status(root,a.search_id)
        elif a.cmd=='verify':result=C.verify(root,a.search_id)
        elif a.cmd=='start':result=C.transition(root,a.search_id,a.trial_id,'running')
        elif a.cmd in ('fail','abort'):result=C.transition(root,a.search_id,a.trial_id,'failed' if a.cmd=='fail' else 'aborted',a.reason)
        elif a.cmd=='tell':result=C.tell(root,a.search_id,a.trial_id,a.artifact,a.run)
        else:result=C.render(root,a.search_id,a.out)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (HarnessError,OSError,ValueError,KeyError) as e:
        print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
