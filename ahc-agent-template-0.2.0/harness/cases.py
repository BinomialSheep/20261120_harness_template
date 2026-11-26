"""Frozen inputs, generator provenance and sets shared by linked worktrees.
The bytes, not just generator seeds, identify an evaluation case.
"""
from __future__ import annotations
import os, re, shutil, sys, tempfile, uuid
from pathlib import Path
from . import storage as S
from .util import HarnessError, contained, digest, load, lock, now, safe_name, save, sha, finite


def freeze(root: Path, spec: dict) -> dict:
    root=Path(root).resolve()
    if spec.get('schema_version') != 1 or not spec.get('cases'):
        raise HarnessError('Nonempty case specification, schema_version=1 required')
    records=[]; seen_ids=set(); seen_bytes=set()
    for row in spec['cases']:
        if set(row)-{'id','path','block','tags','seed','weight'}: raise HarnessError('Unknown case field')
        cid=safe_name(row['id']); block=safe_name(row.get('block',cid))
        source=contained(root,row['path'])
        if not source.is_file(): raise HarnessError('Input must be a file')
        h=sha(source)
        if cid in seen_ids or h in seen_bytes:
            raise HarnessError('Duplicate case ID or bytes: use solver_seeds for repeats, not duplicate independent cases')
        seen_ids.add(cid);seen_bytes.add(h)
        weight=row.get('weight',1.0)
        if not finite(weight) or weight<=0: raise HarnessError('Positive finite case weight required')
        tags=row.get('tags',{})
        if not isinstance(tags,dict) or any(not isinstance(k,str) or not isinstance(v,(str,int,float,bool)) for k,v in tags.items()):
            raise HarnessError('Tags must be a mapping of small scalar values')
        seed=row.get('seed')
        if seed is not None and type(seed)!=int: raise HarnessError('Generator seed is integer or null')
        records.append({'id':cid,'block':block,'file':'inputs/'+cid+'.in','sha256':h,
                        'bytes':source.stat().st_size,'tags':tags,'generator_seed':seed,'weight':weight})
    identity={'schema_version':1,'cases':records,'provenance':spec.get('provenance',{'kind':'import','source':'user supplied inputs'})}
    sid=digest(identity); base=S.evidence(root,'casesets');dest=base/sid
    with lock(S.base(root)/'locks'/('cases-'+sid+'.lock')):
        if not dest.exists():
            temp=Path(tempfile.mkdtemp(prefix='.cases-',dir=base))
            try:
                for row,rec in zip(spec['cases'],records):
                    out=temp/rec['file'];out.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(contained(root,row['path']),out)
                    if sha(out)!=rec['sha256']: raise HarnessError('Input changed while freezing')
                save(temp/'manifest.json',{'case_set_id':sid,'identity':identity,'created_at':now(),'name':spec.get('name','inputs')})
                os.replace(temp,dest)
            finally:
                if temp.exists(): shutil.rmtree(temp)
    return {k:v for k,v in resolve(root,sid).items() if k!='directory'}


def resolve(root: Path, case_set_id: str) -> dict:
    if not re.fullmatch(r'[0-9a-f]{64}',case_set_id): raise HarnessError('Use a complete immutable case_set_id')
    d=S.evidence(root,'casesets')/case_set_id;m=load(d/'manifest.json')
    if m.get('case_set_id')!=case_set_id or digest(m.get('identity'))!=case_set_id: raise HarnessError('Case manifest changed')
    for row in m['identity']['cases']:
        p=contained(d,row['file'])
        if sha(p)!=row['sha256'] or p.stat().st_size!=row['bytes']: raise HarnessError('Frozen input changed: '+row['id'])
    return {**m,'directory':str(d)}


def generate(root: Path, recipe: dict, work: str) -> dict:
    """One process per seed, output on stdout; wrap official multi-file generators in a project adapter.
    Inputs and the generator file snapshot survive the originating worktree. No overwrite/resume guesses.
    """
    from .research import operation, register_child
    from .process import run
    from .resources import reserve as machine_reserve
    if recipe.get('schema_version')!=1 or not recipe.get('files'): raise HarnessError('Generator files are required')
    seeds=recipe.get('seeds',[])
    if not seeds or any(type(x)!=int for x in seeds) or len(seeds)!=len(set(seeds)):
        raise HarnessError('Unique integer generator seeds required')
    command=recipe.get('command',[])
    if not command or any(not isinstance(x,str) for x in command): raise HarnessError('Generator command must be argv')
    hashes={}
    for rel in recipe['files']:
        p=contained(root,rel)
        if not p.is_file(): raise HarnessError('List generator files explicitly')
        hashes[str(p.relative_to(Path(root).resolve()))]=sha(p)
    generator_id=digest({'files':hashes,'command':command,'options':recipe.get('options',{})})
    job=S.evidence(root,'imports')/('generation-'+uuid.uuid4().hex);oracle=job/'generator';oracle.mkdir(parents=True)
    for rel,h in hashes.items():
        out=oracle/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(contained(root,rel),out)
        if sha(out)!=h: raise HarnessError('Generator changed during snapshot')
    manifest={'schema_version':1,'name':recipe.get('name','generated'),'cases':[],
              'provenance':{'kind':'generated','generator_id':generator_id,'files':hashes,'recipe':recipe,'generation_evidence':job.name}}
    save(job/'recipe.json',recipe); save(job/'state.json',{'status':'running'})
    # freeze() expects input paths under the checkout, so stage safely in its ignored work directory.
    stage=Path(root)/'work'/('generate-'+uuid.uuid4().hex);stage.mkdir(parents=True)
    try:
        with operation(root,'case_generation',{'generated_cases':len(seeds)},work) as auth, machine_reserve('shared','generation:'+work):
            for i,seed in enumerate(seeds):
                values={'{python}':sys.executable,'{oracle}':str(oracle),'{seed}':str(seed)}
                argv=list(command)
                for k,v in values.items(): argv=[a.replace(k,v) for a in argv]
                cwd=job/f'case-{i}';cwd.mkdir()
                r=run(argv,cwd=cwd,timeout=recipe.get('timeout_s',30),max_output=recipe.get('max_output_bytes',16777216),
                      on_start=lambda pid:register_child(root,auth,pid))
                save(cwd/'process.json',r)
                if r['status']!='ok': raise HarnessError(f'Generator failed at seed {seed}: {r["status"]}')
                # Native inputs are text; reject non-UTF8 rather than claiming the original bytes were preserved.
                if '\ufffd' in r['stdout']: raise HarnessError('Generator emitted invalid UTF-8; provide a binary-to-text adapter')
                p=stage/f'case-{i:06d}.in';p.write_bytes(r['stdout'].encode())
                manifest['cases'].append({'id':f'case-{i:06d}','path':str(p.relative_to(root)),'seed':seed,
                    'tags':recipe.get('tags',{})})
        for rel,h in hashes.items():
            if sha(oracle/rel)!=h: raise HarnessError('Frozen generator modified itself')
        out=freeze(root,manifest);save(job/'state.json',{'status':'complete','case_set_id':out['case_set_id']})
        return {'case_set_id':out['case_set_id'],'cases':len(seeds),'generator_id':generator_id,'evidence':str(job)}
    except BaseException:
        save(job/'state.json',{'status':'failed','completed':len(manifest['cases']),'staging_preserved':str(stage)})
        raise
    finally:
        # Keep failed/partial generation for diagnosis. Successful inputs have been frozen.
        if (job/'state.json').is_file() and load(job/'state.json')['status']=='complete': shutil.rmtree(stage)
