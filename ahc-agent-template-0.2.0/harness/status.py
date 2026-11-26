"""Derived human brief, not another editable task database."""
import json,shutil,subprocess
from pathlib import Path
from . import storage as S,research as R
from .config import project
from .util import HarnessError,load,now,save

BEGIN='<!-- BEGIN GENERATED -->';END='<!-- END GENERATED -->'
def brief(root):
    s=S.read(root);p=s['payload'];m=p['mission'];lines=[f'> Generated {now()}; store `{s["store_id"]}`, generation {s["generation"]}.',
    '> This is a snapshot. The runtime store, not this file, owns permissions and remaining budget.', '', '## Mission']
    lines += [f'- `{m["id"]}` — **{m["status"]}**: {m["goal"]}'] if m else ['No research mission yet. Setup/questions do not authorize training or expensive evaluation.']
    lines += ['', '## Shared references']
    for name in ('baseline','best_local','submit_candidate'):
        lines.append(f'- {name}: `{p["refs"].get(name,{}).get("artifact_id","not set")}`')
    lines+=['','## Budget','| Unit | Limit | Charged | Held | Remaining |','|---|---:|---:|---:|---:|']
    for u,b in R.budget(p).items():lines.append(f'| {u} | {b["limit"]} | {b["used"]} | {b["held"]} | {b["remaining"]} |')
    lines+=['','## Work / processes']
    held=[r for r in p['reservations'].values() if r['status']=='held']
    for r in held:
        live=sum(R.alive(q) for q in r['processes']);lines.append(f'- `{r["work"]}`: held, {live} registered live/unknown-host process(es).')
    if not held:lines.append('No held reservations.')
    lines+=['','## Tasks (Beads)']
    bd=shutil.which('bd')
    if not bd:lines.append('Beads not installed in this environment. Do not infer an empty task queue.')
    else:
        from integrations.beads.bridge import query
        try:
            ready=query(root,['ready','--exclude-type','epic','--exclude-label','proposal','--limit','0']);started=query(root,['list','--status','in_progress','--limit','0'])
            lines.append(f'{len(ready)} dependency-ready tasks; {len(started)} in_progress records (not process liveness).')
            lines.extend(f'- `{x["id"]}` {x.get("title","")}' for x in ready[:5])
        except (HarnessError,OSError): lines.append('Beads unavailable; runtime evidence remains intact. Do not reinitialize an existing DB.')
    lines+=['','Full task list: Beads. Questions/notes: `human/`. No automatic background research is implied.']
    return '\n'.join(lines)+'\n'

def render(root):
    S.require_integrator(root,'STATUS render');s=S.read(root);text=brief(root)
    path=Path(root)/'docs/STATUS.md';old=path.read_text() if path.exists() else '# STATUS\n\n'
    block=BEGIN+'\n'+text+END
    if BEGIN in old and END in old: new=old[:old.index(BEGIN)]+block+old[old.index(END)+len(END):]
    else:new=old.rstrip()+'\n\n'+block+'\n'
    path.write_text(new,encoding='utf-8')
    # Reviewable projection: no full inputs, absolute paths or authentication data.
    m=s['payload']['mission']; snap={'schema_version':1,'store_id':s['store_id'],'generation':s['generation'],'created_at':now(),
      'mission':{k:m.get(k) for k in ('id','goal','status','revision','limits')} if m else None,'budget':R.budget(s['payload']),
      'shared_refs':{k:v['artifact_id'] for k,v in s['payload']['refs'].items() if not k.startswith('dev:')}}
    save(Path(root)/'research/snapshot.json',snap)
    return {'status':'docs/STATUS.md','generation':s['generation'],'snapshot_is_authority':False}
