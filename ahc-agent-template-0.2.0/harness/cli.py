from __future__ import annotations
import argparse,json,os,shutil,sys
from pathlib import Path
from . import __version__
from .config import root as find_root,read,project
from .util import HarnessError,load,save,contained,sha
from . import storage as S,research as R,artifacts as A,workspace as W,evidence as E,experiments as X

def amounts(texts):
    out={}
    for t in texts or []:
        k,sep,v=t.partition('=')
        if not sep:raise HarnessError('Use UNIT=NUMBER')
        out[k]=float(v)
    return out

def doctor(root):
    result={'template_version':__version__,'python':sys.version.split()[0],'platform':sys.platform,
      'git':shutil.which('git'),'g++':shutil.which('g++'),'cargo':shutil.which('cargo'),'beads':shutil.which('bd'),
      'configured':(root/'project.json').exists(),'network_used':False,'atcoder_service_clients':False}
    try:
        result['workspace']=W.info(root);result['mission']=(S.read(root)['payload']['mission'] or {}).get('status','none')
    except HarnessError as e:result['state_unavailable']=str(e)
    try:
        contract=load(root/'problem/contract.json')
        result['problem_contract']={'verified':contract.get('verified',False),'mode':contract.get('mode'),'evidence':contract.get('evidence',[])}
    except (OSError,ValueError):result['problem_contract']={'verified':False,'error':'missing/invalid'}
    result['limitations']=['Linux/WSL; not an OS sandbox','Interactive tester owns per-turn limits and hidden observations',
       'No survival guarantee after VS Code/WSL exit','Supplied problem integration and Beads native acceptance need separate validation',
       'No embedded contest AI rules; no automatic rule inference or contest submission']
    return result

def main():
    p=argparse.ArgumentParser(description='AHC agent harness');s=p.add_subparsers(dest='command',required=True)
    s.add_parser('doctor');s.add_parser('init');s.add_parser('render');s.add_parser('status')
    a=s.add_parser('build');a.add_argument('source');a.add_argument('--name',required=True);a.add_argument('--profile',default='release');a.add_argument('--asset',action='append',default=[])
    a=s.add_parser('pack');a.add_argument('artifact');a.add_argument('--out',required=True)
    a=s.add_parser('eval');a.add_argument('--candidate',required=True);a.add_argument('--baseline',required=True);a.add_argument('--suite',required=True);a.add_argument('--work',required=True);a.add_argument('--label',default='evaluation');a.add_argument('--jobs',type=int,default=1);a.add_argument('--audit-approval')
    a=s.add_parser('report');a.add_argument('run_id')
    a=s.add_parser('precision');a.add_argument('run_id');a.add_argument('--halfwidth',type=float,required=True);a.add_argument('--solver-run-budget',type=int);a.add_argument('--candidates',type=int,default=1)
    a=s.add_parser('next-action');a.add_argument('--state',required=True)
    a=s.add_parser('exposures');a.add_argument('--purpose',choices=['smoke','dev','diagnostic','stress','audit']);a.add_argument('--case-set-id')

    a=s.add_parser('replay');a.add_argument('run_id');a.add_argument('--trial',required=True);a.add_argument('--work',required=True)
    a=s.add_parser('input');a.add_argument('--text',required=True);a.add_argument('--kind',choices=sorted(R.KINDS),default='idea');a.add_argument('--source',choices=['human','ai','review'],default='human')
    a=s.add_parser('mission');ss=a.add_subparsers(dest='action',required=True)
    q=ss.add_parser('start');q.add_argument('--spec',required=True);q.add_argument('--input',required=True)
    q=ss.add_parser('amend');q.add_argument('--changes',required=True);q.add_argument('--input',required=True);q.add_argument('--reason',required=True);q.add_argument('--expected-revision',required=True,type=int)
    q=ss.add_parser('state');q.add_argument('state',choices=['active','paused','stopped','completed']);q.add_argument('--reason',required=True)
    a=s.add_parser('work');ss=a.add_subparsers(dest='action',required=True)
    q=ss.add_parser('reserve');q.add_argument('id');q.add_argument('--op',action='append',required=True);q.add_argument('--estimate',action='append',required=True);q.add_argument('--experiment')
    q=ss.add_parser('settle');q.add_argument('id');q.add_argument('--outcome',choices=['done','failed','released'],required=True);q.add_argument('--usage',action='append',default=[])
    q=ss.add_parser('run');q.add_argument('id');q.add_argument('--op',choices=['training','case_generation'],required=True);q.add_argument('--timeout',type=float,default=3600);q.add_argument('--amount',action='append',default=[]);q.set_defaults(argv=[])
    a=s.add_parser('workspace');ss=a.add_subparsers(dest='action',required=True);ss.add_parser('info');ss.add_parser('check-remove')
    q=ss.add_parser('create');q.add_argument('path');q.add_argument('--branch',required=True)
    a=s.add_parser('adopt');a.add_argument('artifact');a.add_argument('--ref',required=True);a.add_argument('--expected',required=True,help='previous artifact ID or NONE');a.add_argument('--reason',required=True);a.add_argument('--evidence',action='append',required=True)
    a=s.add_parser('evidence');ss=a.add_subparsers(dest='action',required=True)
    q=ss.add_parser('seal');q.add_argument('path');q.add_argument('--kind',choices=['datasets','models','sealed'],required=True);q.add_argument('--producer');q.add_argument('--key',action='append',default=[])
    q=ss.add_parser('verify');q.add_argument('id');q.add_argument('--kind',required=True)
    a=s.add_parser('experiment');ss=a.add_subparsers(dest='action',required=True)
    q=ss.add_parser('init');q.add_argument('--plan',required=True)
    for name in ('amend','close'):
        q=ss.add_parser(name);q.add_argument('id');q.add_argument('--data',required=True)
    a=s.add_parser('backup');a.add_argument('--out',required=True)
    a=s.add_parser('restore');a.add_argument('--from',dest='source',required=True);a.add_argument('--expected-store-id',required=True)
    a=s.add_parser('cases');ss=a.add_subparsers(dest='action',required=True)
    q=ss.add_parser('import');q.add_argument('--spec',required=True)
    q=ss.add_parser('verify');q.add_argument('id')
    q=ss.add_parser('generate');q.add_argument('--recipe',required=True);q.add_argument('--work',required=True)
    a=s.add_parser('judge');a.add_argument('--case-set',required=True);a.add_argument('--case',required=True)
    a.add_argument('--output',required=True);a.add_argument('--contract',default='problem/contract.json');a.add_argument('--work',required=True)
    raw=sys.argv[1:]; command_argv=[]
    if raw[:2]==['work','run'] and '--' in raw:
        sep=raw.index('--');command_argv=raw[sep+1:];raw=raw[:sep]
    a=p.parse_args(raw);root=find_root()
    if a.command=='work' and a.action=='run':a.argv=command_argv
    try:
        if a.command=='cases':
            from . import cases
            if a.action=='import':out=cases.freeze(root,load(contained(root,a.spec)))
            elif a.action=='generate':out=cases.generate(root,load(contained(root,a.recipe)),a.work)
            else:out=cases.resolve(root,a.id)
        elif a.command=='judge':
            from .judge import score_output
            out=score_output(root,a.case_set,a.case,a.output,a.contract,a.work)
        elif a.command=='doctor':out=doctor(root)
        elif a.command=='init':out=S.init(root);out={k:out[k] for k in ('store_id','generation')}
        elif a.command=='status':
            from .status import brief
            print(brief(root));return 0
        elif a.command=='render':
            from .status import render
            out=render(root)
        elif a.command=='build':out=A.build(root,a.source,a.name,a.profile,a.asset)
        elif a.command=='pack':
            from .bundle import pack
            art=A.resolve(root,a.artifact);path=contained(root,a.out,exists=False);out=pack(art,path)
            # Compile exactly what is sent without the source artifact's command-line macro switches.
            built=A.build(root,str(path.relative_to(root)),'packed-'+out['sha256'][:12],'release');out['packed_artifact_id']=built['artifact_id'];out['verification']='compiled; behavioral tests still required'
            save(path.with_suffix(path.suffix+'.manifest.json'),out)
        elif a.command=='eval':
            from .evaluate import run_evaluation
            out=run_evaluation(root,a.suite,a.candidate,a.baseline,a.label,a.jobs,a.audit_approval,a.work)
        elif a.command=='report':out=load(contained(S.evidence(root,'runs'),a.run_id)/'summary.json')
        elif a.command=='precision':
            from .precision import forecast
            out=forecast(root,a.run_id,a.halfwidth,a.solver_run_budget,a.candidates)
        elif a.command=='next-action':
            from .portfolio import recommend
            out=recommend(load(contained(root,a.state)))
        elif a.command=='exposures':
            from . import exposure as H
            if a.purpose and a.case_set_id:
                from . import cases as C
                m=C.resolve(root,a.case_set_id)
                out=H.preview(root,a.purpose,[c['sha256'] for c in m['identity']['cases']])
            elif not a.purpose and not a.case_set_id:
                from collections import Counter
                rows=H.history(root)
                out={'exposures':len(rows),'by_purpose':dict(Counter(x['purpose'] for x in rows)),
                    'warning':'Case/hash exposure ledger is in the shared local store, not Git.'}
            else:raise HarnessError('exposures: supply both --purpose and --case-set-id, or neither')

        elif a.command=='replay':
            from .replay import replay
            out=replay(root,a.run_id,a.trial,a.work)
        elif a.command=='input':out=R.input_record(root,a.text,a.kind,a.source)
        elif a.command=='mission':
            if a.action=='start':out=R.mission_new(root,load(a.spec),a.input)
            elif a.action=='amend':out=R.amend(root,load(a.changes),a.reason,a.input,a.expected_revision)
            else:out=R.mission_state(root,a.state,a.reason)
        elif a.command=='work':
            if a.action=='reserve':out=R.reserve(root,a.id,a.op,amounts(a.estimate),a.experiment)
            elif a.action=='settle':out=R.settle(root,a.id,a.outcome,amounts(a.usage))
            else:
                argv=a.argv[1:] if a.argv and a.argv[0]=='--' else a.argv
                out=R.run_command(root,a.id,a.op,argv,a.timeout,amounts(a.amount) if a.amount else None)
        elif a.command=='workspace':
            if a.action=='info':out=W.info(root)
            elif a.action=='create':out=W.create(root,a.path,a.branch)
            else:out=W.check_remove(root)
        elif a.command=='adopt':out=A.adopt(root,a.ref,a.artifact,None if a.expected=='NONE' else a.expected,a.reason,a.evidence)
        elif a.command=='evidence':
            out=E.seal(root,a.path,a.kind,load(a.producer) if a.producer else None,a.key) if a.action=='seal' else E.verify(root,a.kind,a.id)
        elif a.command=='experiment':
            out=X.init(root,load(a.plan)) if a.action=='init' else X.change(root,a.id,amendment=load(a.data) if a.action=='amend' else None,result=load(a.data) if a.action=='close' else None)
        elif a.command=='backup':out=S.backup(root,a.out)
        elif a.command=='restore':out=S.restore(root,a.source,a.expected_store_id)
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
        if a.command=='eval' and out['summary']['decision']=='invalid':return 2
        if a.command=='work' and a.action=='run' and out['status']!='ok':return 2
        return 0
    except (HarnessError,OSError,ValueError,KeyError) as e:
        print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
