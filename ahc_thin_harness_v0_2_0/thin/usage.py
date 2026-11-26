"""Token usage of workers and interactive sessions. Read-only, except that a codex worker's own rollout
is moved out of ~/.codex/sessions into its job directory.

Categories are the same for both engines: in=uncached input, cw=cache write, cr=cache read,
out=output including thinking, think=the thinking part of out. Codex input_tokens includes the cached
part, so it is subtracted. ctx is the input read by one call (in+cw+cr). Limits are percentages of the
subscription's 5-hour and weekly windows. No dollar figures: the plans are subscriptions.

Interactive calls are attributed to a round only when they are round work (see attribute):
the session must have run that round's harness commands, and the call must fall between the start of
the turn that ran `prepare` and the end of the turn that ran `close`. Everything else is OUTSIDE.
"""
from __future__ import annotations
import bisect,datetime as dt,json,os,re,shutil
from pathlib import Path

KEYS=('in','cw','cr','out','think')
CODEX_WINDOWS={300:'five_hour',10080:'seven_day'}
OUTSIDE='ラウンド外'
CMD_RE=re.compile(r'-m\s+thin\.cli\s+(?:--root\s+\S+\s+)?([a-z][a-z-]*)(?:\s+(R\d{4,}))?')
ROUND_CMDS={'prepare','go','run','submit','fail','accept','rerun','workers-done','evaluate','promote','close'}
TAG_RE=re.compile(r'<([a-z][\w-]*)[^>]*>.*?</\1>',re.S)

def claude_home():return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home()/'.claude')
def codex_home():return Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')

def parse_ts(s):
    if not s:return None
    if isinstance(s,(int,float)):return dt.datetime.fromtimestamp(s,dt.timezone.utc)
    try:return dt.datetime.fromisoformat(s.replace('Z','+00:00'))
    except ValueError:return dt.datetime.fromisoformat(s[:19]+'+00:00')

def lines(path):
    with open(path,encoding='utf-8',errors='replace') as f:
        for line in f:
            try:d=json.loads(line)
            except json.JSONDecodeError:continue
            if isinstance(d,dict):yield d

def claude_usage(u):
    return {'in':u.get('input_tokens') or 0,'cw':u.get('cache_creation_input_tokens') or 0,
            'cr':u.get('cache_read_input_tokens') or 0,'out':u.get('output_tokens') or 0,
            'think':(u.get('output_tokens_details') or {}).get('thinking_tokens') or 0}

def codex_usage(u):
    cached=u.get('cached_input_tokens') or 0
    return {'in':(u.get('input_tokens') or 0)-cached,'cw':u.get('cache_write_input_tokens') or 0,'cr':cached,
            'out':u.get('output_tokens') or 0,'think':u.get('reasoning_output_tokens') or 0}

def ctx(u):return u['in']+u['cw']+u['cr']

def total(usages):
    t={'calls':0,**{k:0 for k in KEYS},'peak_ctx':0}
    for u in usages:
        t['calls']+=1;t['peak_ctx']=max(t['peak_ctx'],ctx(u))
        for k in KEYS:t[k]+=u[k]
    return t

def _claude_limits(d):
    for name,w in ((d.get('rate_limit_info') or {}).get('unifiedWindows') or {}).items():
        if isinstance(w,dict) and w.get('utilization') is not None:
            yield name,w.get('resetsAt'),round(w['utilization']*100,1)

def _codex_limits(rl):
    for key in ('primary','secondary'):
        w=(rl or {}).get(key)
        if isinstance(w,dict) and w.get('used_percent') is not None:
            yield CODEX_WINDOWS.get(w.get('window_minutes'),key),w.get('resets_at'),w['used_percent']

RESET_DRIFT=120  # Codex's resets_at moves by a second between readings of the same window period

def _period(keys,name,reset):
    for n,r in keys:
        if n==name and (r==reset or (isinstance(r,(int,float)) and isinstance(reset,(int,float)) and abs(r-reset)<=RESET_DRIFT)):
            return n,r
    return name,reset

def limit_spans(obs):
    """[(window, resets_at, percent)] in time order -> first and last percent per window period."""
    g={}
    for name,reset,pct in obs:g.setdefault(_period(g,name,reset),[]).append(pct)
    return [{'window':n,'resets_at':r,'first':v[0],'last':v[-1]} for (n,r),v in g.items()]

def merge_spans(spans):
    """Spans from parallel workers -> lowest first and highest last per window period."""
    g={}
    for s in spans:
        k=_period(g,s['window'],s['resets_at']);x=g.setdefault(k,dict(s))
        x['first']=min(x['first'],s['first']);x['last']=max(x['last'],s['last'])
    return list(g.values())

def _commands(text,t):
    return [(t,sub,rid or None) for sub,rid in CMD_RE.findall(text) if sub in ROUND_CMDS]

def _prompt_text(content):
    text=content if isinstance(content,str) else ' '.join(x.get('text','') for x in content if isinstance(x,dict) and x.get('type')=='text')
    tags=re.findall(r'<([a-z][\w-]*)',text);rest=' '.join(TAG_RE.sub(' ',text).split())
    return rest[:60] or (f'({tags[0]})' if tags else '')

def claude_log(path):
    """Calls [(time, usage)] deduplicated by message.id (one response is written as several lines carrying
    the same usage), user turns [(start, prompt)], harness commands [(time, subcommand, round)]."""
    calls,turns,cmds,meta={},[],[],{'title':None,'first':None,'last':None}
    for d in lines(path):
        t=d.get('timestamp');m=d.get('message')
        if t:meta['first']=meta['first'] or t;meta['last']=t
        if d.get('type')=='ai-title':meta['title']=d.get('aiTitle')
        if not isinstance(m,dict):continue
        c=m.get('content')
        if d.get('type')=='user' and not d.get('isMeta') and not d.get('isCompactSummary') and (isinstance(c,str) or (isinstance(c,list) and not any(
                isinstance(x,dict) and x.get('type')=='tool_result' for x in c))):
            turns.append((parse_ts(t),_prompt_text(c)))
        if d.get('type')!='assistant':continue
        if m.get('usage') and m.get('model')!='<synthetic>':
            calls.setdefault(m.get('id') or d.get('uuid'),(parse_ts(t),claude_usage(m['usage'])))
        for x in c or []:
            if isinstance(x,dict) and x.get('type')=='tool_use' and isinstance(x.get('input'),dict):
                cmds+=_commands(str(x['input'].get('command','')),parse_ts(t))
    turns.sort(key=lambda x:x[0] or dt.datetime.min.replace(tzinfo=dt.timezone.utc))  # file order is not time order
    return {'calls':list(calls.values()),'turns':turns,'cmds':cmds,**meta}

def claude_calls(path):
    log=claude_log(path);return log['calls'],log

def codex_calls(path):
    """[(time, usage)] per model call of a codex rollout (differences of the cumulative token_count),
    session metadata (with turns and harness commands), and limit observations."""
    calls,limits,prev,meta=[],[],None,{'turns':[],'cmds':[],'waits':0,'clock_checks':0}
    for d in lines(path):
        p=d.get('payload') if isinstance(d.get('payload'),dict) else {};t=d.get('timestamp')
        if d.get('type')=='session_meta':
            meta.update(cwd=p.get('cwd',''),originator=p.get('originator'),first=t,last=t)
        if d.get('type')=='event_msg' and p.get('type')=='task_started':meta['turns'].append((parse_ts(t),''))
        if d.get('type')=='event_msg' and p.get('type')=='user_message' and meta['turns']:
            meta['turns'][-1]=(meta['turns'][-1][0],' '.join(str(p.get('message','')).split())[:60])
        if d.get('type')=='response_item' and p.get('type') not in ('message','reasoning','additional_tools'):
            meta['cmds']+=_commands(json.dumps(p,ensure_ascii=False),parse_ts(t))
        if d.get('type')=='response_item' and p.get('type') in ('custom_tool_call','function_call'):
            # Calls spent only on waiting for a running command or reading the clock: each re-reads the context.
            # codex names them `wait` (2026-11) or `write_stdin` with nothing to write (earlier versions).
            meta['waits']+=is_wait(p);meta['clock_checks']+='curr_time' in f"{p.get('input') or p.get('arguments') or ''}"
        if p.get('type')!='token_count':continue
        meta['last']=t
        limits+=_codex_limits(p.get('rate_limits'))
        if not p.get('info'):continue
        cum=codex_usage(p['info'].get('total_token_usage') or {})
        delta=cum if prev is None else {k:cum[k]-prev[k] for k in KEYS}
        if any(v<0 for v in delta.values()):delta=codex_usage(p['info'].get('last_token_usage') or {})
        prev=cum
        if any(delta.values()):calls.append((parse_ts(t),delta))
    meta['turns'].sort(key=lambda x:x[0] or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    return calls,meta,limits

def is_wait(p):
    name=p.get('name','');args=p.get('arguments') or p.get('input') or ''
    if name in ('wait','sleep'):return True
    if name!='write_stdin':return False
    try:a=json.loads(args) if isinstance(args,str) else args
    except ValueError:return False
    return isinstance(a,dict) and not a.get('chars')

def collect_codex_rollout(jdir):
    """Move this codex worker's rollout from ~/.codex/sessions to jobs/<id>/codex_rollout.jsonl.
    It holds per-call token counts even when the worker was killed, and later workers cannot read it there."""
    jdir=Path(jdir);out=jdir/'agent.stdout';dest=jdir/'codex_rollout.jsonl'
    if dest.exists() or not out.exists():return None
    tid=None
    for d in lines(out):
        if d.get('type')=='thread.started':tid=d.get('thread_id');break
    if not isinstance(tid,str) or not re.fullmatch(r'[0-9A-Za-z-]{8,64}',tid):return None
    found=sorted((codex_home()/'sessions').rglob(f'rollout-*{tid}.jsonl'))
    if not found:return None
    shutil.move(str(found[0]),str(dest))
    return dest

def worker_summary(jdir):
    """Usage of one worker from its own logs, or None when the logs hold none (claude text output)."""
    jdir=Path(jdir);rollout=jdir/'codex_rollout.jsonl';out=jdir/'agent.stdout'
    calls,limits,source,extra=[],[],None,{}
    if rollout.exists():
        calls,meta,limits=codex_calls(rollout);calls=[u for _,u in calls];source='codex-rollout'
        extra={'waits':meta['waits'],'clock_checks':meta['clock_checks']}
    elif out.exists():
        seen,turns,final=set(),[],None
        for d in lines(out):
            t=d.get('type');m=d.get('message')
            if t=='turn.completed':turns.append(codex_usage(d.get('usage') or {}))
            elif t=='assistant' and isinstance(m,dict) and m.get('usage'):
                if m.get('id') not in seen:seen.add(m.get('id'));calls.append(claude_usage(m['usage']))
            elif t=='rate_limit_event':limits+=_claude_limits(d)
            elif t=='result' and isinstance(d.get('usage'),dict):final=claude_usage(d['usage'])
        if calls:source='claude-stream'
        elif turns:source='codex-turns'
    if source is None:return None
    if source=='codex-turns':  # only totals per turn: no call count, no context size
        return {'source':source,'calls':None,**{k:sum(u[k] for u in turns) for k in KEYS},'peak_ctx':None,'limits':[]}
    out={'source':source,**total(calls),**extra,'limits':limit_spans(limits)}
    if source=='claude-stream' and final:  # in stream-json the per-message output counts are partial; the result has the totals
        out.update({k:final[k] for k in KEYS})
    return out

def last_message(jdir):
    """Final agent message from a JSON event log (claude stream-json or codex --json); None for plain text."""
    out=Path(jdir)/'agent.stdout'
    if not out.exists():return None
    msg,structured=None,False
    for d in lines(out):
        t=d.get('type');m=d.get('message');it=d.get('item') if isinstance(d.get('item'),dict) else {}
        if t=='result' and isinstance(d.get('result'),str):msg,structured=d['result'],True
        elif t=='assistant' and isinstance(m,dict):
            structured=True
            text=''.join(c.get('text','') for c in m.get('content') or [] if isinstance(c,dict) and c.get('type')=='text')
            if text:msg=text
        elif t=='item.completed' and it.get('type')=='agent_message':msg,structured=it.get('text'),True
    return msg if structured else None

def project_dir(root):
    return claude_home()/'projects'/re.sub(r'[^A-Za-z0-9]','-',str(Path(root).resolve()))

def sessions(root,since=None):
    """Interactive Claude Code / Codex sessions started in this repository (workers excluded):
    [{source, owner, title, first, last, calls, turns, cmds}]. A subagent entry has the parent session as owner
    and shares its turns. since: skip files last written before this POSIX time."""
    root=Path(root).resolve();res=[];pd=project_dir(root)
    old=lambda f:since is not None and f.stat().st_mtime<since
    if pd.is_dir():
        for f in sorted(pd.glob('*.jsonl')):
            if old(f):continue
            log=claude_log(f);src='claude:'+f.stem[:8]
            res.append({'source':src,'owner':src,**log})
            subs=sorted((pd/f.stem/'subagents').glob('*.jsonl'))
            sub=[c for s in subs for c in claude_log(s)['calls']]
            if sub:res.append({'source':src+'/sub','owner':src,'title':f'subagents of {src} ({len(subs)})',
                               'first':log['first'],'last':log['last'],'calls':sub,'turns':log['turns'],'cmds':[]})
    sd=codex_home()/'sessions';runs=str(root/'runs')+os.sep
    if sd.is_dir():
        for f in sorted(sd.rglob('rollout-*.jsonl')):
            if old(f):continue
            with open(f,encoding='utf-8',errors='replace') as h:head=h.readline()
            if str(root) not in head:continue  # session_meta (first line) holds cwd
            calls,meta,_=codex_calls(f);cwd=meta.get('cwd','')
            if not (cwd==str(root) or cwd.startswith(str(root)+os.sep)) or cwd.startswith(runs):continue
            src='codex:'+f.stem.split('-')[-1][:8]
            res.append({'source':src,'owner':src,'title':f"{meta.get('originator')} {cwd[len(str(root)):] or '/'}",
                        'first':meta.get('first'),'last':meta.get('last'),'calls':calls,'turns':meta['turns'],'cmds':meta['cmds']})
    return res

def rounds(root):
    rs=[]
    for rd in sorted((Path(root)/'runs').glob('R[0-9]*')):
        try:plan=json.loads((rd/'plan.json').read_text());state=json.loads((rd/'state.json').read_text())
        except (OSError,ValueError):continue
        rs.append({'id':rd.name,'mode':plan.get('mode'),'created':parse_ts(plan.get('created_at')),
                   'closed':parse_ts(state.get('closed_at')),'dir':rd})
    return rs

def _turn(turns,t):
    """Index of the turn that contains time t (turns are sorted by start)."""
    return bisect.bisect_right([x[0] for x in turns],t)-1 if t else -1

def round_spans(rs,sess):
    """Per round: owners (sessions that ran its harness commands), begin, end (close), and the turn that ran close.
    begin is the start of the turn that ran prepare; when that session ran no earlier round (a session started
    for this round), it is the start of the session's first turn. Never before the previous close."""
    info={r['id']:{'owners':set(),'begin':None,'end':r['closed'],'close_turn':None,'prep':None,'prep_first':None} for r in rs}
    for s in sess:
        if s['owner']!=s['source']:continue
        for t,sub,rid in s['cmds']:
            if t is None:continue
            if sub in ('prepare','round') and not rid:  # the round it created starts within 10 minutes
                rid=next((r['id'] for r in rs if r['created'] and 0<=(r['created']-t).total_seconds()<=600),None)
                if rid:
                    k=_turn(s['turns'],t);start=s['turns'][k][0] if k>=0 else t
                    p=info[rid]['prep'];info[rid]['prep']=start if p is None else min(p,start)
                    info[rid]['prep_session']=s['source'];info[rid]['prep_first']=s['turns'][0][0] if s['turns'] else start
            if rid in info:
                info[rid]['owners'].add(s['source'])
                if sub=='close':info[rid]['close_turn']=(s['source'],_turn(s['turns'],t))
    prev=None;seen=set()
    for r in rs:
        x=info[r['id']];b=x['prep'] or r['created']
        if x.get('prep_session') and x['prep_session'] not in seen:b=x['prep_first']
        seen|=x['owners']
        x['begin']=max(b,prev) if prev and b else (b or prev)
        prev=r['closed'] or prev
    return info

def attribute(rs,sess,info=None):
    """[(round or OUTSIDE, source, turn index, time, usage)] for every interactive call."""
    info=info or round_spans(rs,sess);ids=[r['id'] for r in rs];by={s['source']:s for s in sess};out=[]
    for s in sess:
        owner=by.get(s['owner'],s)
        for t,u in s['calls']:
            k=_turn(owner['turns'],t);rid=OUTSIDE
            for i,r in enumerate(ids):
                x=info[r]
                if s['owner'] not in x['owners'] or t is None or not x['begin']:continue
                if x['begin']<=t and (x['end'] is None or t<=x['end']):rid=r;break
                nxt=info[ids[i+1]]['begin'] if i+1<len(ids) else None
                if x['close_turn']==(s['owner'],k) and t>x['end'] and (nxt is None or t<=nxt):rid=r;break  # report after close
            out.append((rid,s['source'],k,t,u))
    return out

def worker_rows(rd):
    rows={}
    for jdir in sorted((Path(rd)/'jobs').glob('*')):
        try:job=json.loads((jdir/'job.json').read_text())
        except (OSError,ValueError):job={}
        u=job['usage'] if 'usage' in job else worker_summary(jdir)
        rows[jdir.name]={'engine':job.get('engine') or ({'claude-stream':'claude'}.get(u['source'],'codex') if u else None),
                         'model':job.get('model'),'effort':job.get('effort'),'outcome':job.get('agent_outcome'),
                         'minutes':round((job.get('agent_run') or {}).get('seconds',0)/60,1),'usage':u}
    return rows

def round_snapshot(root,rid):
    """Usage of one round, written to runs/<rid>/usage.json (transcripts in ~/.claude expire).
    final is false until a later round exists: the report after close may still be running."""
    rs=rounds(root);ids=[r['id'] for r in rs];i=ids.index(rid)
    start=rs[i-1]['closed'] if i else None
    sess=sessions(root,since=start.timestamp() if start else None)
    info=round_spans(rs,sess);x=info[rid]
    orch={}
    for r,src,_,_,u in attribute(rs,sess,info):
        if r==rid:orch.setdefault(src,[]).append(u)
    titles={s['source']:s['title'] for s in sess}
    workers=worker_rows(rs[i]['dir']);used=[w['usage'] for w in workers.values() if w['usage']]
    iso=lambda t:t.isoformat() if t else None
    return {'round_id':rid,'mode':rs[i]['mode'],'final':i+1<len(rs),
            'span':{'from':iso(x['begin']),'close':iso(x['end'])},'owners':sorted(x['owners']),
            'orchestrator':{src:{**total(us),'title':titles.get(src)} for src,us in orch.items()},
            'workers_total':{k:sum(u[k] for u in used) for k in KEYS},
            'workers_without_usage':sum(1 for w in workers.values() if not w['usage']),
            'worker_limits':merge_spans([s for u in used for s in u.get('limits') or []]),
            'workers':workers}

def save_snapshots(root,upto=None):
    """Write usage.json for closed rounds that lack one or whose snapshot is not final. Returns written ids."""
    written=[]
    for r in rounds(root):
        if not r['closed'] or (upto and r['id']>upto):continue
        p=r['dir']/'usage.json'
        try:old=json.loads(p.read_text())
        except (OSError,ValueError):old=None
        if old and old.get('final') and 'orchestrator' in old:continue
        from .util import write_json
        write_json(p,round_snapshot(root,r['id']));written.append(r['id'])
    return written
