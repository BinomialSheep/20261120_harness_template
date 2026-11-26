"""Re-score a saved output with a frozen supplied judge, without re-running the solver."""
import shutil,sys,uuid
from pathlib import Path
from . import cases as C,storage as S
from .ahc_contract import validate,validate_result
from .research import operation,register_child
from .resources import reserve as machine_reserve
from .process import run
from .util import HarnessError,contained,load,save,sha,now


def score_output(root,case_set_id,case_id,output,contract_path,work):
    root=Path(root).resolve();m=C.resolve(root,case_set_id)
    case=next((c for c in m['identity']['cases'] if c['id']==case_id),None)
    if not case:raise HarnessError('Unknown case')
    c=validate(load(contained(root,contract_path)))
    if c['mode']=='interactive':raise HarnessError('A transcript is not a batch answer; provide a separate output-only contract to analyze it')
    source=Path(output).resolve()
    if not source.is_file():raise HarnessError('Saved output missing')
    with operation(root,'rescore',{'judge_runs':1},work) as auth,machine_reserve('shared','rescore'):
        rd=S.evidence(root,'runs')/('judge-'+uuid.uuid4().hex);rd.mkdir();oracle=rd/'oracle_snapshot';hashes={}
        for rel in [contract_path,'harness','adapters/ahc',*c['files']]:
            p=contained(root,rel)
            for f in (p.rglob('*') if p.is_dir() else [p]):
                if f.is_file() and '__pycache__' not in f.parts and f.suffix!='.pyc':
                    contained(root,f);key=str(f.relative_to(root));h=sha(f);out=oracle/key;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(f,out)
                    if sha(out)!=h:raise HarnessError('Scorer changed during freeze')
                    hashes[key]=h
        out=rd/'saved-output.txt';h=sha(source);shutil.copyfile(source,out)
        if sha(out)!=h:raise HarnessError('Output changed during freeze')
        q={'contract':{**c,'mode':'output_only'},'work_dir':str(rd),'oracle_root':str(oracle),'solver_seed':0,'variant':{},
           'input_path':str(Path(m['directory'])/case['file']),'input_sha256':case['sha256'],
           'precomputed_output':str(out),'precomputed_sha256':h}
        save(rd/'request.json',q)
        p=run([sys.executable,'-S',str(oracle/'adapters/ahc/run_case.py'),str(rd/'request.json'),str(rd/'response.json')],
            cwd=rd,timeout=c['limits']['judge_wall_s']+10,env={'PYTHONPATH':str(oracle)},on_start=lambda pid:register_child(root,auth,pid))
        if p['status']!='ok':raise HarnessError('Scorer driver failed: '+p['status'])
        r=validate_result(load(rd/'response.json'))
        for rel,h0 in hashes.items():
            if sha(oracle/rel)!=h0:raise HarnessError('Frozen scorer mutated')
        save(rd/'receipt.json',{'at':now(),'case_set_id':case_set_id,'case_id':case_id,'input_sha256':case['sha256'],
            'output_sha256':h,'oracle_files':hashes,'result':r})
        return {'run_id':rd.name,'result':r,'path':str(rd)}
