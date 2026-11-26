"""Cross-experiment holdout exposure gate, shared by worktrees within one clone.

The gate is intentionally conservative. It protects immutable input *bytes*, not suite filenames,
seed labels or experiment numbers. A deliberately predeclared multi-arm audit needs a future
explicit protocol; silently treating its second look as fresh would be worse.
"""
from __future__ import annotations
import json
from . import storage as S
from .util import HarnessError,canonical,lock,now

EXPOSED = ('smoke','dev','diagnostic','stress','audit')

def history(root):
    p=S.base(root)/'exposures.jsonl'
    if not p.is_file():return []
    try:
        items=[json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]
        for row in items:
            if not isinstance(row.get('input_hashes'),list) or row.get('purpose') not in EXPOSED:
                raise ValueError('unexpected exposure schema')
        return items
    except (ValueError,KeyError) as exc:
        raise HarnessError(f'Exposure ledger damaged; stop, inspect, do not reset: {exc}') from exc

def preview(root,purpose,input_hashes):
    hashes=set(input_hashes)
    if purpose not in EXPOSED or not hashes or len(hashes)!=len(input_hashes):
        raise HarnessError('Exposure needs valid purpose and unique frozen input hashes')
    rows=history(root)
    overlap=[]
    for row in rows:
        shared=hashes.intersection(row['input_hashes'])
        if shared:overlap.append({'run_id':row['run_id'],'purpose':row['purpose'],'n_inputs':len(shared)})
    # Any prior use compromises a claim of a *fresh independent confirmation*.
    if purpose=='audit' and overlap:
        raise HarnessError(f'Audit inputs were already exposed (even under other suite IDs): {overlap[:5]}')
    # Once audited, do not accidentally reuse those bytes to tune the candidate later.
    if purpose!='audit' and any(row['purpose']=='audit' for row in overlap):
        raise HarnessError(f'Previously audited inputs cannot be used for tuning or repeated testing: {overlap[:5]}')
    return {'fresh':not overlap,'overlap':overlap,'audit_eligible':purpose=='audit' and not overlap,
            'warning':'Freshness is local to this clone and ledger; not an OS/security isolation boundary.'}

def register(root,run_id,purpose,suite_hash,case_set_id,input_hashes,blocks,approval=None):
    p=S.base(root)/'exposures.jsonl'
    with lock(S.base(root)/'locks/exposures.lock'):
        checked=preview(root,purpose,input_hashes)  # hold same lock for check + append
        row={'schema_version':2,'run_id':run_id,'purpose':purpose,'suite_sha256':suite_hash,
             'case_set_id':case_set_id,'input_hashes':input_hashes,'blocks':blocks,'at':now(),
             'approval_reference':approval, 'overlap_count':len(checked['overlap'])}
        with p.open('a',encoding='utf-8') as f:
            f.write(canonical(row).decode()+'\n');f.flush()
    return {'recorded':True,'fresh':checked['fresh'],'overlap_count':len(checked['overlap']),
            'unverified_approval_reference':approval}
