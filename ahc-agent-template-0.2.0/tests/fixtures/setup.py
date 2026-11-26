"""Reusable fixture setup for offline tests and the demonstration command."""
import random
from pathlib import Path
from harness.cases import freeze
from harness.util import load,save

def prepare(root,kind='max',n=24):
    root=Path(root);d=root/'work/inputs'/kind;d.mkdir(parents=True,exist_ok=True);rows=[]
    for i in range(n):
        p=d/f'{i}.in'
        if kind=='interactive':p.write_text(str(30+i)+'\n')
        else:
            r=random.Random(i);p.write_text(f'12 3 {i}\n'+ ' '.join(map(str,r.sample(range(1,1000),12)))+'\n')
        rows.append({'id':f'c{i:04d}','path':str(p.relative_to(root)),'seed':i,'tags':{'size':'small','group':i%2}})
    m=freeze(root,{'schema_version':1,'name':'synthetic-'+kind,'cases':rows})
    c=load(root/'problem/contract.json');c['verified']=True;c['evidence']=['Synthetic test fixture only: NOT an official AHC validation']
    c['limits'].update(solver_wall_s=2,judge_wall_s=5,tester_wall_s=10)
    if kind=='interactive':
        c['mode']='interactive';c['files']=['tests/fixtures/referee.py']
        c['tester_command']=['{python}','-S','{oracle}/tests/fixtures/referee.py','{input}','{solver}','{result}']
        c.pop('judge_command',None)
    else:
        c['mode']='batch';c['files']=['tests/fixtures/judge.py','tests/fixtures/visualizer.py']
        c['judge_command']=['{python}','-S','{oracle}/tests/fixtures/judge.py','{input}','{output}','{result}']
        c['visualizer_command']=['{python}','-S','{oracle}/tests/fixtures/visualizer.py','{input}','{output}','{visualization}']
    save(root/f'work/{kind}-contract.json',c)
    suite={'schema_version':1,'mode':'ahc','purpose':'dev','contract':f'work/{kind}-contract.json','case_set_id':m['case_set_id'],
       'solver_seeds':[0],'metric':{'name':'fixture_score','direction':'minimize' if kind=='min' else 'maximize',
          'comparison':'difference','aggregation':'case_mean'},
       'variants':{'candidate':{'args':['--'+kind] if kind!='interactive' else []},
                   'baseline':{'args':['--linear'] if kind=='interactive' else []}},
       'minimum_blocks':20,'run_budget_s':120,'timing_sensitive':False}
    path=f'work/{kind}-suite.json';save(root/path,suite);return path,suite
