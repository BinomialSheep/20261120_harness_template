"""Paired single-solver metrics. Solver repeats are averaged inside cases; blocks are resampled.
Raw scores, comparison metrics, and a contest-wide rank/relative score are distinct.
"""
from __future__ import annotations
import math, random, statistics
from collections import Counter, defaultdict
from .util import HarnessError, finite


def effect(candidate,baseline,metric):
    sign=1 if metric['direction']=='maximize' else -1
    if metric['comparison']=='difference':return sign*(candidate-baseline)
    if candidate<=0 or baseline<=0:raise HarnessError('log_ratio requires strictly positive valid scores')
    return sign*(math.log(candidate)-math.log(baseline))


def percentile(xs,p):
    xs=sorted(xs)
    if not xs:return None
    x=(len(xs)-1)*p;lo=int(x);hi=min(lo+1,len(xs)-1)
    return xs[lo]+(xs[hi]-xs[lo])*(x-lo)


def summarize(records,plan,*,resamples=2000):
    expected=plan['trials'];by={};dups=[];metadata={t['trial_id']:t for t in expected}
    for r in records:
        if r['trial_id'] in by:dups.append(r['trial_id'])
        by[r['trial_id']]=r
    missing=sorted(set(metadata)-set(by));extra=sorted(set(by)-set(metadata))
    counts=Counter(r.get('status','unknown') for r in records)
    byrole={role:dict(Counter(by[t['trial_id']]['status'] for t in expected
                             if t['role']==role and t['trial_id'] in by)) for role in ('candidate','baseline')}
    out={'schema_version':1,'planned_trials':len(expected),'recorded_trials':len(records),'status_counts':dict(counts),
         'status_by_role':byrole,'missing':missing,'duplicates':dups,'extra':extra,'metric':plan['metric'],
         'decision':'invalid','mean_effect':None,'ci95':None,'n_blocks':0,'case_effects':[],
         'contest_score':None,'inference':'predeclared input population; block-cluster bootstrap',
         'caution':'Exploratory interval; not corrected for tuning/repeated peeking. No automatic adoption.'}
    timing={}
    for role in ('candidate','baseline'):
        vals=[]
        for t in expected:
            if t['role']!=role:continue
            r=by.get(t['trial_id'],{});m=r.get('metrics',{})
            val=m.get('processes',{}).get('solver',{}).get('wall_s')
            if val is None:val=m.get('solver_wall_s')
            if finite(val):vals.append(val)
        timing[role]={'count':len(vals),'mean_s':statistics.mean(vals) if vals else None,
                      'p95_s':percentile(vals,.95),'max_s':max(vals) if vals else None,
                      'note':'Local wall time, not official execution time. Null is not zero.'}
    out['timing']=timing
    if missing or extra or dups:return out
    cases={c['id']:c for c in plan['cases']};pairs=defaultdict(dict)
    for t in expected:pairs[(t['case_id'],t['repeat'])][t['role']]=by[t['trial_id']]
    values=defaultdict(list);raw=defaultdict(lambda:defaultdict(list));bad=set();reasons=[]
    for (cid,rep),pair in pairs.items():
        if set(pair)!={'candidate','baseline'}:bad.add(cid);continue
        if any(p['status']!='ok' or p.get('valid') is not True or not finite(p.get('raw_score')) for p in pair.values()):
            bad.add(cid);continue
        try:d=effect(pair['candidate']['raw_score'],pair['baseline']['raw_score'],plan['metric'])
        except HarnessError as e:bad.add(cid);reasons.append(str(e));continue
        values[cid].append(d)
        for role,r in pair.items():raw[cid][role].append(r['raw_score'])
    # Any failure in a case excludes that case from the diagnostic view only; it invalidates the overall comparison.
    for cid in sorted(cases):
        c=cases[cid]
        out['case_effects'].append({'case_id':cid,'block':c['block'],'tags':c.get('tags',{}),
            'valid_pairs':len(values[cid]),'complete':cid not in bad,
            'effect':statistics.mean(values[cid]) if cid not in bad and values[cid] else None,
            'candidate':statistics.mean(raw[cid]['candidate']) if cid not in bad and raw[cid]['candidate'] else None,
            'baseline':statistics.mean(raw[cid]['baseline']) if cid not in bad and raw[cid]['baseline'] else None})
    out['invalid_cases']=sorted(bad)
    diagnostic=[r for r in out['case_effects'] if r['effect'] is not None]
    out['diagnostic_only']={'improved':sum(r['effect']>0 for r in diagnostic),
        'worsened':sum(r['effect']<0 for r in diagnostic),'equal':sum(r['effect']==0 for r in diagnostic),
        'worst_cases':[r['case_id'] for r in sorted(diagnostic,key=lambda r:r['effect'])[:10]],
        'usable_for_adoption':False}
    if bad or len(diagnostic)!=len(cases):
        out['reason']='Failures or undefined comparison; valid-only diagnostics do not establish improvement'
        if reasons:out['metric_errors']=sorted(set(reasons))
        return out
    blocks=defaultdict(list)
    for r in diagnostic:blocks[r['block']].append((r['effect'],cases[r['case_id']].get('weight',1)))
    sums=[(sum(v*w for v,w in rows),sum(w for v,w in rows)) for _,rows in sorted(blocks.items())]
    estimand=plan['metric'].get('aggregation','case_mean')
    def average(bs):
        if estimand=='block_mean':return statistics.mean(a/b for a,b in bs)
        return sum(a for a,b in bs)/sum(b for a,b in bs)
    avg=average(sums);rng=random.Random(plan.get('bootstrap_seed',0))
    draws=[average(rng.choices(sums,k=len(sums))) for _ in range(resamples)]
    ci=[percentile(draws,.025),percentile(draws,.975)];threshold=plan.get('minimum_effect',0)
    decision=('insufficient_blocks' if len(sums)<plan.get('minimum_blocks',20) else
              'positive_evidence' if ci[0]>threshold else 'negative_evidence' if ci[1]<-threshold else 'inconclusive')
    wsum=sum(c.get('weight',1) for c in cases.values())
    out.update(mean_effect=avg,ci95=ci,n_blocks=len(sums),decision=decision,
        raw_score_aggregate={role:{'sum_case_means':sum(r[role] for r in diagnostic),
            'weighted_mean':sum(r[role]*cases[r['case_id']].get('weight',1) for r in diagnostic)/wsum}
            for role in ('candidate','baseline')},
        block_effects={k:sum(v*w for v,w in rows)/sum(w for v,w in rows) for k,rows in blocks.items()})
    return out
