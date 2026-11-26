"""Minimal local command-line surface for orchestrator, workers and evaluator."""
import argparse,json,sys,tempfile,shutil
from pathlib import Path
from . import __version__
from . import core as C,scoring as E
from .util import ThinError,read_json,root_from,write_json

def parser():
    p=argparse.ArgumentParser(prog='python -m thin.cli',description='AHC thin harness; round A (10) or B (1)')
    p.add_argument('--root',help='Project root with config.json (defaults to current directory)')
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('init-best');a.add_argument('source')
    a=sub.add_parser('prepare');a.add_argument('mode',choices=['A','B']);a.add_argument('--ideas');a.add_argument('--idea')
    a=sub.add_parser('run-workers');a.add_argument('round_id');a.add_argument('--engine',required=True)
    a=sub.add_parser('submit');a.add_argument('round_id');a.add_argument('job_id')
    a=sub.add_parser('fail');a.add_argument('round_id');a.add_argument('job_id');a.add_argument('--reason',required=True)
    a=sub.add_parser('workers-done');a.add_argument('round_id')
    a=sub.add_parser('evaluate');a.add_argument('round_id')
    a=sub.add_parser('promote');a.add_argument('round_id');a.add_argument('job_id')
    a=sub.add_parser('close');a.add_argument('round_id')
    sub.add_parser('status')
    a=sub.add_parser('round');a.add_argument('mode',choices=['A','B']);a.add_argument('--ideas');a.add_argument('--idea');a.add_argument('--engine',required=True)
    a=sub.add_parser('demo');a.add_argument('--keep',help='Keep the generated toy project at this target directory')
    return p

def demo(path):
    from .util import now
    src=Path(__file__).resolve().parents[1];dest=Path(path)
    if dest.exists() and any(dest.iterdir()):raise ThinError('Demo destination must be absent or empty')
    dest.mkdir(parents=True,exist_ok=True)
    shutil.copytree(src/'thin',dest/'thin',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(src/'scripts',dest/'scripts')
    (dest/'problem/tools').mkdir(parents=True)
    shutil.copyfile(src/'examples/toy/statement.md',dest/'problem/statement.md')
    shutil.copyfile(src/'examples/toy/tools/score_case.py',dest/'problem/tools/score_case.py')
    shutil.copyfile(src/'examples/toy/config.json',dest/'config.json')
    (dest/'demo_assets').mkdir()
    for file in ('fake_agent.py','optimized.cpp'):
        shutil.copyfile(src/'examples/toy'/file,dest/'demo_assets'/('toy_optimized.cpp' if file.endswith('.cpp') else file))
    C.init_best(dest,src/'examples/toy/baseline.cpp')
    ideas={'ideas':[{'kind':'continue','idea':f'Synthetic local mutation variant {i}: select the largest K elements', 'parents':['best']} for i in range(1,11)]}
    write_json(dest/'ideas.json',ideas)
    prep=C.prepare(dest,'A',ideas_path=dest/'ideas.json')
    workers=C.run_workers(dest,prep['round_id'],'demo')
    results=E.evaluate(dest,prep['round_id'])
    rec=results['recommended']
    promoted=E.promote(dest,prep['round_id'],rec) if rec else None
    C.close(dest,prep['round_id'])
    return {'toy_project':str(dest),'prepared':prep,'workers':len(workers['workers']),
            'recommended':rec,'promoted':bool(promoted),'candidate_count':len(results['candidates']),
            'valid_count':sum(x['valid'] for x in results['candidates']),
            'evaluation_cases':results['seed_count'], 'best':C.read_best(dest)}

def main(argv=None):
    p=parser();a=p.parse_args(argv)
    try:
        if a.command=='demo':
            if a.keep:result=demo(Path(a.keep).resolve())
            else:
                with tempfile.TemporaryDirectory(prefix='ahc-thin-demo-') as d:
                    result=demo(Path(d)/'workspace')
        else:
            root=Path(a.root).resolve() if a.root else root_from()
            if a.command=='init-best':result=C.init_best(root,a.source)
            elif a.command=='prepare':result=C.prepare(root,a.mode,a.ideas,a.idea)
            elif a.command=='run-workers':result=C.run_workers(root,a.round_id,a.engine)
            elif a.command=='submit':result=C.submit(root,a.round_id,a.job_id)
            elif a.command=='fail':result=C.fail_job(root,a.round_id,a.job_id,a.reason)
            elif a.command=='workers-done':result=C.mark_workers_done(root,a.round_id)
            elif a.command=='evaluate':result=E.evaluate(root,a.round_id)
            elif a.command=='promote':result=E.promote(root,a.round_id,a.job_id)
            elif a.command=='close':result=C.close(root,a.round_id)
            elif a.command=='status':result=C.status(root)
            elif a.command=='round':
                if a.engine=='manual':raise ThinError('Use prepare / submit / workers-done / evaluate for manual workers')
                prep=C.prepare(root,a.mode,a.ideas,a.idea)
                workers=C.run_workers(root,prep['round_id'],a.engine)
                result={'prepared':prep,'workers':workers,'evaluation':E.evaluate(root,prep['round_id'])}
            else:raise ThinError('Unknown command')
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False));return 0
    except (ThinError,OSError,ValueError,KeyError,TypeError) as e:
        print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
