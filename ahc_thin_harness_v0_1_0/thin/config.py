from pathlib import Path
import copy,math,sys
from .util import ThinError,read_json,relpath,format_argv,digest

def load(root):
    root=Path(root);c=read_json(root/'config.json')
    if c.get('schema_version')!=1:raise ThinError('config.json schema_version=1 required')
    try:
        prob=c['problem'];sol=c['solver'];judge=c['judge'];ev=c['evaluation'];workers=c['workers']
        statement=relpath(root,prob['statement']);tools=relpath(root,prob['tools'])
        if not statement.is_file() or not tools.is_dir():raise ThinError('Missing problem statement/tools')
        name=sol['filename']
        if name!='main.cpp':raise ThinError('v0.1 requires main.cpp, adjust problem-specific implementation through config')
        format_argv(sol['build'],dict(source='main.cpp',binary='main',workspace='.',python=sys.executable))
        format_argv(judge['command'],dict(binary='main',seed='0',result='result.json',tools='.',workspace='.',python=sys.executable,workdir='.'))
        if judge['direction'] not in ('maximize','minimize'):raise ThinError('judge.direction must be maximize/minimize')
        if judge['metric'] not in ('paired_difference','paired_log_ratio'):raise ThinError('judge.metric must be paired_difference/paired_log_ratio')
        if not isinstance(judge['timeout_sec'],(float,int)) or judge['timeout_sec']<=0:raise ThinError('Positive judge timeout required')
        if type(ev['count'])!=int or ev['count']<=0 or type(ev['first_seed'])!=int or ev['first_seed']<1:raise ThinError('Positive count and first seed>=1 required')
        if type(ev['parallelism'])!=int or not 1<=ev['parallelism']<=10:raise ThinError('evaluation parallelism between 1 and 10 required')
        if not isinstance(ev['promotion_margin'],(float,int)) or not math.isfinite(ev['promotion_margin']) or ev['promotion_margin']<0:raise ThinError('Nonnegative finite promotion margin required')
        if type(workers['parallelism_A'])!=int or not 1<=workers['parallelism_A']<=10:raise ThinError('workers.parallelism_A must be 1..10')
        for kind in ('A','B'):
            if not isinstance(workers[f'minutes_{kind}'],(float,int)) or workers[f'minutes_{kind}']<=0:raise ThinError('Worker duration must be positive')
            seeds=workers[f'debug_seeds_{kind}']
            if not isinstance(seeds,list) or not seeds or seeds[0]!=0 or len(set(seeds))!=len(seeds) or any(type(x)!=int or x<0 for x in seeds):raise ThinError('Debug seed list must start at 0 with unique nonnegative ints')
            if len(seeds)> (1 if kind=='A' else 20):raise ThinError('A: seed0 only; B: at most 20 debug seeds')
        for command in workers['engines'].values():format_argv(command,dict(workspace='.',python=sys.executable))
        return c
    except (KeyError,TypeError) as e:raise ThinError(f'Invalid config: {e}') from e

def context(root,config,workspace,binary,seed,result,workdir):
    return {'workspace':str(Path(workspace).resolve()), 'tools':str((Path(workspace)/'problem'/'tools').resolve()),
            'python':sys.executable,'binary':str(Path(binary).resolve()),'seed':str(seed),
            'result':str(Path(result).resolve()),'workdir':str(Path(workdir).resolve())}
