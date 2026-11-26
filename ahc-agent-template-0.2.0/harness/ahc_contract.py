"""Technical execution contract, not a contest participation/AI policy."""
from .util import HarnessError, finite
STATUSES={'ok','solver_timeout','solver_crash','solver_invalid','solver_output_limit',
          'solver_memory_limit','infrastructure_error','not_run'}

def validate(c):
    if c.get('schema_version')!=1: raise HarnessError('Contract schema_version=1 required')
    if c.get('mode') not in ('batch','interactive','output_only'): raise HarnessError('Unknown execution mode')
    if type(c.get('verified')) is not bool: raise HarnessError('Contract needs verified boolean')
    if not isinstance(c.get('files'),list) or not c['files']: raise HarnessError('List scorer/tester dependencies')
    limits=c.get('limits',{})
    for k in ('solver_wall_s','judge_wall_s','tester_wall_s'):
        if not finite(limits.get(k)) or limits[k]<=0: raise HarnessError('Positive '+k+' required')
    for k in ('max_output_bytes','max_stderr_bytes'):
        if type(limits.get(k)) is not int or limits[k]<1: raise HarnessError('Positive integer '+k+' required')
    for k in ('solver_memory_mb','solver_cpu_s'):
        if limits.get(k) is not None and (type(limits[k]) is not int or limits[k]<=0): raise HarnessError('Optional positive integer '+k+' required')
    key='tester_command' if c['mode']=='interactive' else 'judge_command'
    argv=c.get(key)
    if not isinstance(argv,list) or not argv or any(not isinstance(x,str) for x in argv): raise HarnessError(key+' must be argv')
    if not any('{result}' in s for s in argv): raise HarnessError('Scorer/tester must write {result} JSON')
    if c['mode']=='interactive' and not any('{solver}' in s for s in argv): raise HarnessError('Tester must launch {solver}')
    return c

def validate_result(r):
    if not isinstance(r,dict) or r.get('schema_version')!=1 or r.get('status') not in STATUSES:
        raise HarnessError('Invalid result schema/status')
    if r['status']=='ok':
        if r.get('valid') is not True or not finite(r.get('raw_score')):
            raise HarnessError('ok requires valid=true and a finite raw_score, not solver self-report')
    elif r.get('valid') is True or r.get('raw_score') is not None:
        raise HarnessError('Failure results must have valid=false and raw_score=null')
    return r
