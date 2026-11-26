"""Create round, run independent workers and manage candidate submissions.

One orchestrator is assumed. Locks prevent accidental duplicate orchestration but do not make
non-cooperating background tools safe. Workers see isolated copies, not prior research logs.
"""
from __future__ import annotations
import concurrent.futures, contextlib, fcntl, hashlib,json,os,re,shutil,sys, time
from pathlib import Path
from .config import load as load_config
from .process import execute
from .util import ThinError,read_json,write_json,now,digest,safe_id,relpath,format_argv

IGNORE={'target','.git','__pycache__','node_modules','.venv','.cache'}

@contextlib.contextmanager
def locked(root):
    with (Path(root)/'.thin.lock').open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(f,fcntl.LOCK_UN)

def _copy_dir(source,dest):
    for p in source.rglob('*'):
        if p.is_symlink():raise ThinError('Tool directory contains symlink; resolve carefully before copying: '+str(p))
    shutil.copytree(source,dest,ignore=lambda d,n:[name for name in n if name in IGNORE])

def tool_hash(source):
    h=hashlib.sha256()
    for f in sorted(Path(source).rglob('*')):
        if f.is_file() and not any(p in IGNORE for p in f.relative_to(source).parts):
            h.update(str(f.relative_to(source)).encode());h.update(digest(f).encode())
    return h.hexdigest()

def read_best(root):
    p=Path(root)/'best'/'best.json'
    if not p.exists():return None
    b=read_json(p);source=Path(root)/'best'/'main.cpp'
    if not source.exists() or digest(source)!=b['sha256']:raise ThinError('Current best was modified outside promotion flow')
    return b

def init_best(root,source):
    root=Path(root);config=load_config(root)
    with locked(root):
        if (root/'best/best.json').exists():raise ThinError('Best already exists; use promote/explicit rollback')
        src=Path(source).resolve()
        if not src.is_file():raise ThinError('Initial best source missing')
        dest=root/'best';dest.mkdir(exist_ok=True)
        shutil.copyfile(src,dest/'main.cpp')
        meta={'sha256':digest(dest/'main.cpp'),'source':str(src),'at':now(),'origin':'initial baseline'}
        write_json(dest/'best.json',meta)
    return meta

def _resolve_parent(root,ref):
    root=Path(root)
    if ref=='best':
        b=read_best(root)
        if not b:raise ThinError('No best candidate registered')
        return root/'best/main.cpp'
    if not isinstance(ref,str) or not re.fullmatch(r'R\d{4,}/(?:A\d{2}|B01)',ref):
        raise ThinError('Parent must be best or R0001/A01 etc')
    round_id,job_id=ref.split('/')
    p=root/'runs'/round_id/'jobs'/job_id/'candidate.cpp'
    if not p.is_file():raise ThinError('Parent candidate source missing: '+ref)
    job=read_json(p.parent/'job.json')
    if job['status'] not in ('submitted','scored') or digest(p)!=job.get('sha256'):
        raise ThinError('Parent candidate has not been frozen')
    return p

def _ideas(mode,idea_json,idea_text):
    if mode=='A':
        if not idea_json:raise ThinError('Action A requires --ideas: 10 genuinely distinct proposals')
        d=read_json(idea_json);d=d.get('ideas') if isinstance(d,dict) else d
        if not isinstance(d,list) or len(d)!=10:raise ThinError('Exactly 10 ideas required for A')
        ideas=[]
        for i,x in enumerate(d,1):
            if not isinstance(x,dict) or x.get('kind') not in ('new','continue','combine') or not isinstance(x.get('idea'),str) or len(x['idea'].strip())<12:
                raise ThinError(f'Idea {i} missing kind or meaningful hypothesis')
            parents=x.get('parents',[])
            if not isinstance(parents,list) or len(parents)>2 or len(set(parents))!=len(parents):raise ThinError('At most two distinct parents')
            if x['kind']=='new' and parents:raise ThinError('New idea should not depend on parent source')
            if x['kind']=='continue' and len(parents)!=1:raise ThinError('Continuation needs exactly one parent')
            if x['kind']=='combine' and len(parents)!=2:raise ThinError('Combination needs two parents')
            ideas.append({'id':f'A{i:02d}','kind':x['kind'],'idea':x['idea'].strip(),'parents':parents})
        if len({x['idea'] for x in ideas})!=10:raise ThinError('Duplicate ideas')
        return ideas
    if mode=='B':
        if not idea_text or len(idea_text.strip())<12:raise ThinError('B needs a meaningful --idea, not an empty request')
        return [{'id':'B01','kind':'deep','idea':idea_text.strip(),'parents':['best']}]
    raise ThinError('mode must be A or B')

def _task_text(mode,job,deadline_minutes,debug_seeds):
    p='、'.join(f'parent_{i}.cpp' for i in range(1,len(job['parents'])+1))
    seed_info='seed0だけ' if mode=='A' else ('許可されたdebug seedだけ（最大20ケース）')
    return f'''# 独立したAHC改善workerの指示（{mode}）

## 目的
{job['idea']}

## 入力
- 問題文: `problem/statement.md`
- 公式ローカルツールのコピー: `problem/tools/`
- このタスクだけの親ソース: {p or 'なし（新規実装）'}
- **過去の実験履歴、他workerの成果、全体ランキング、以前の考察は渡していない**。それらを探しに行かない。

## 成果物
- `main.cpp` を作成または修正し、最終的な**コンパイル可能なC++23ソルバー**を提出する。
- 任意で短い `notes.md`（何を変更したかと、確かめられなかった点）を残す。

## 実行予算と禁止事項
- このworkerの上限は **{deadline_minutes:g}分**。時間を使い切る必要はない。
- **{seed_info}での動作確認のみ**。`python3 quickcheck.py --seed 0` が用意されている。許可seed: {debug_seeds}。
- **1000ケースの性能評価、大量のシード生成、並列ベンチ、長時間学習はしない**。それらは全worker完了後にオーケストレーターが順番・同条件で実施する。
- 他workerや親プロジェクトのファイルは変更せず、このworkspaceの中だけを編集する。
- 公式ツールのコピーは試験用。提出ソースは一つの `main.cpp` とする。
- 自信がなくても最後に現時点の最良コードを `main.cpp` に確定する。出力のない場合は失敗扱いになる。
'''

def prepare(root,mode,ideas_path=None,idea_text=None):
    root=Path(root);config=load_config(root);ideas=_ideas(mode,ideas_path,idea_text)
    b=read_best(root)
    # Reject bad parent references *before* creating a partially populated round.
    for x in ideas:
        for parent in x['parents']:_resolve_parent(root,parent)
    with locked(root):
        runs=root/'runs';runs.mkdir(exist_ok=True)
        for path in runs.glob('R*/state.json'):
            st=read_json(path)
            if st.get('phase')!='closed':raise ThinError(f'Previous round {path.parent.name} not closed; resume/close it first')
        ids=[int(p.name[1:]) for p in runs.iterdir() if p.is_dir() and re.fullmatch(r'R\d+',p.name)]
        rnum=max(ids,default=0)+1;rid=f'R{rnum:04d}';rd=runs/rid;rd.mkdir()
        ev=config['evaluation'];start=ev['first_seed']+(rnum-1)*ev['count'];seeds=list(range(start,start+ev['count']))
        if set(seeds)&set(config['workers']['debug_seeds_A']+config['workers']['debug_seeds_B']):
            raise ThinError('Evaluation seeds overlap worker debug seeds; change first_seed')
        if b:shutil.copyfile(root/'best'/'main.cpp',rd/'baseline.cpp')
        snapshot={'schema_version':1,'round_id':rid,'number':rnum,'mode':mode,'created_at':now(),
          'config':config,'seed_start':start,'seed_count':ev['count'],'best_before':b,'ideas':ideas}
        write_json(rd/'plan.json',snapshot)
        source_tools=relpath(root,config['problem']['tools'])
        _copy_dir(source_tools,rd/'judge_tools')
        shutil.copyfile(relpath(root,config['problem']['statement']),rd/'judge_statement.md')
        snapshot['tools_sha256']=tool_hash(rd/'judge_tools');snapshot['statement_sha256']=digest(rd/'judge_statement.md')
        write_json(rd/'plan.json',snapshot)
        for idea in ideas:
            jobid=idea['id'];jdir=rd/'jobs'/jobid;w=jdir/'workspace';(w/'problem').mkdir(parents=True)
            _copy_dir(rd/'judge_tools',w/'problem'/'tools')
            shutil.copyfile(rd/'judge_statement.md',w/'problem'/'statement.md')
            parents=[]
            for ix,ref in enumerate(idea['parents'],1):
                source=_resolve_parent(root,ref);target=w/f'parent_{ix}.cpp';shutil.copyfile(source,target)
                parents.append({'ref':ref,'sha256':digest(target)})
            # Create task and self-contained quickcheck config, never copy history / run logs / repo metadata.
            mini={'build':config['solver']['build'],'judge':config['judge']['command'],
                  'judge_timeout':config['judge']['timeout_sec'],
                  'allowed_seeds':config['workers'][f'debug_seeds_{mode}']}
            write_json(w/'worker_config.json',mini)
            shutil.copyfile(Path(__file__).resolve().parents[1]/'scripts'/'quickcheck.py',w/'quickcheck.py')
            if (root/'demo_assets/fake_agent.py').exists():
                shutil.copyfile(root/'demo_assets/fake_agent.py',w/'fake_agent.py')
                shutil.copyfile(root/'demo_assets/toy_optimized.cpp',w/'toy_optimized.cpp')
            (w/'task.md').write_text(_task_text(mode,{**idea,'parents':parents},config['workers'][f'minutes_{mode}'],mini['allowed_seeds']),encoding='utf-8')
            job={'id':jobid,'status':'pending','idea':idea['idea'],'kind':idea['kind'],'parents':parents,'created_at':now()}
            write_json(jdir/'job.json',job)
            # Independent git repository with no parent history, so codex exec can operate from this cwd.
            p=execute(['git','init','-q'],w,20,stdout_file=jdir/'git.stdout',stderr_file=jdir/'git.stderr')
            if p['status']!='ok':raise ThinError('Cannot initialize isolated worker Git repo: '+str(p))
        write_json(rd/'state.json',{'phase':'prepared','round_id':rid,'prepared_at':now()})
    return {'round_id':rid,'mode':mode,'workers':len(ideas),'seed_start':start,'seed_count':ev['count'],
            'note':'Workspaces contain only task/problem/tools/selected parent code, not previous experiments'}

def _freeze_candidate(jdir,job):
    source=jdir/'workspace'/'main.cpp'
    if not source.is_file() or source.stat().st_size<10:
        job['status']='failed';job['error']='main.cpp missing or empty'
    else:
        shutil.copyfile(source,jdir/'candidate.cpp');job['sha256']=digest(jdir/'candidate.cpp');job['status']='submitted'
        job['submitted_at']=now()
    write_json(jdir/'job.json',job)
    return job

def submit(root,rid,jid):
    rid=safe_id(rid);jid=safe_id(jid);jdir=Path(root)/'runs'/rid/'jobs'/jid
    with locked(root):
        job=read_json(jdir/'job.json')
        if read_json(Path(root)/'runs'/rid/'state.json')['phase']!='prepared':raise ThinError('Can only submit during prepared phase')
        if job['status'] not in ('pending','worker_failed'):
            raise ThinError('Submission state already terminal: '+job['status'])
        return _freeze_candidate(jdir,job)

def _worker(root,rid,jid,command,minutes):
    jdir=Path(root)/'runs'/rid/'jobs'/jid;w=jdir/'workspace'
    with (jdir/'launch.lock').open('a') as lockfile:
        try:fcntl.flock(lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return {'id':jid,'status':'running_elsewhere','note':'Existing worker holds process lock; do not double-launch'}
        try:
            job=read_json(jdir/'job.json')
            if job['status'] in ('submitted','scored'):
                return {'id':jid,'status':job['status'],'reused':True}
            if job['status'] not in ('pending','worker_failed','running'):
                return {'id':jid,'status':job['status']}
            job.update(status='running',worker_started_at=now());write_json(jdir/'job.json',job)
            env=os.environ.copy()
            # Retain user's CLI authentication, but clamp common library oversubscription.
            for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','RAYON_NUM_THREADS','CARGO_BUILD_JOBS'):
                env[name]='1'
            from .config import context
            argv=format_argv(command,context(root,{},w,w/'main.cpp',0,w/'_quick_result.json',w))
            result=execute(argv,w,minutes*60,stdin=(w/'task.md').read_text(),
                           stdout_file=jdir/'agent.stdout',stderr_file=jdir/'agent.stderr',env=env)
            job['agent_run']=result
            frozen=_freeze_candidate(jdir,job)
            if frozen['status']=='submitted':frozen['agent_outcome']=result['status']
            else:
                frozen['agent_outcome']=result['status'];frozen['status']='worker_failed'
            write_json(jdir/'job.json',frozen)
            return {'id':jid,'status':frozen['status'],'agent':result['status'],'seconds':result['seconds']}
        finally:fcntl.flock(lockfile,fcntl.LOCK_UN)

def run_workers(root,rid,engine):
    root=Path(root);rid=safe_id(rid);rd=root/'runs'/rid;plan=read_json(rd/'plan.json');cfg=plan['config']
    phase=read_json(rd/'state.json')['phase']
    if phase not in ('prepared','workers_done'):raise ThinError('Cannot launch workers after evaluation or close')
    if engine=='manual':return {'round_id':rid,'manual':True,'tasks':[str(rd/'jobs'/x['id']/'workspace'/'task.md') for x in plan['ideas']]}
    if engine not in cfg['workers']['engines']:raise ThinError('Engine not configured: '+engine)
    workers=cfg['workers'];parallel=workers['parallelism_A'] if plan['mode']=='A' else 1
    command=workers['engines'][engine]
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        jobs={pool.submit(_worker,root,rid,x['id'],command,workers[f'minutes_{plan["mode"]}']):x for x in plan['ideas']}
        results=[]
        for task in concurrent.futures.as_completed(jobs):results.append(task.result())
    result=sorted(results,key=lambda x:x['id'])
    if any(x['status']=='running_elsewhere' for x in result):
        raise ThinError('Other running worker owns a job; cannot declare all workers finished')
    with locked(root):
        write_json(rd/'state.json',{'phase':'workers_done','round_id':rid,'workers_at':now(),'engine':engine})
    return {'round_id':rid,'workers':result}

def mark_workers_done(root,rid):
    rd=Path(root)/'runs'/safe_id(rid);plan=read_json(rd/'plan.json')
    statuses=[read_json(rd/'jobs'/x['id']/'job.json')['status'] for x in plan['ideas']]
    if any(s not in ('submitted','failed','worker_failed','scored') for s in statuses):
        raise ThinError('Some manual workers still pending; submit them or mark failed')
    write_json(rd/'state.json',{'phase':'workers_done','round_id':rid,'workers_at':now(),'engine':'manual'})
    return {'round_id':rid,'submitted':statuses.count('submitted'),'failed':len(statuses)-statuses.count('submitted')}

def fail_job(root,rid,jid,reason):
    if not reason:raise ThinError('Reason required')
    jdir=Path(root)/'runs'/safe_id(rid)/'jobs'/safe_id(jid)
    with locked(root):
        job=read_json(jdir/'job.json')
        if read_json(Path(root)/'runs'/rid/'state.json')['phase']!='prepared':raise ThinError('Can only submit during prepared phase')
        if job['status'] not in ('pending','worker_failed'):raise ThinError('Already submitted/scored')
        job.update(status='failed',error=reason);write_json(jdir/'job.json',job)
    return job

def close(root,rid):
    root=Path(root);rd=root/'runs'/safe_id(rid)
    with locked(root):
        state=read_json(rd/'state.json')
        if state['phase']=='workers_done':
            jobs=[read_json(j/'job.json')['status'] for j in (rd/'jobs').glob('*') if (j/'job.json').exists()]
            if any(x in ('submitted','scored') for x in jobs):
                raise ThinError('Some candidate submissions are waiting for evaluation')
        elif state['phase']!='evaluated':
            raise ThinError('Evaluate or finish a fully failed round before closing')
        state.update(phase='closed',closed_at=now());write_json(rd/'state.json',state)
    return state

def status(root):
    root=Path(root);best=read_best(root);rows=[]
    for d in sorted((root/'runs').glob('R*/state.json')) if (root/'runs').exists() else []:
        st=read_json(d);rd=d.parent;result=read_json(rd/'result.json') if (rd/'result.json').exists() else {}
        rows.append({'round':rd.name,'phase':st['phase'],'mode':read_json(rd/'plan.json')['mode'],
                    'best_in_round':result.get('recommended')})
    return {'best':best,'rounds':rows[-12:]}
