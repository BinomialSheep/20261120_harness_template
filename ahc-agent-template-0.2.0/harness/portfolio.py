"""Deadline-aware *advisory* research action policy.

No model-dependent confidence, invented improvement rate, policy engine or daemon.
A default baseline that is inspectable, replaceable and useful before meta-learning data exist.
Input is a human/agent-authored, evidence-linked state snapshot. It does not grant permission.
"""
from __future__ import annotations
from datetime import datetime,timezone
from .util import HarnessError,finite

def recommend(state,clock=None):
    if state.get('schema_version')!=1:raise HarnessError('portfolio state schema_version=1 required')
    if type(state.get('baseline_valid')) is not bool:raise HarnessError('baseline_valid is required boolean')
    if type(state.get('oracle_verified')) is not bool:raise HarnessError('oracle_verified is required boolean')
    if type(state.get('evaluation_calibrated')) is not bool:raise HarnessError('evaluation_calibrated is required boolean')
    if not isinstance(state.get('evidence_refs',[]),list):raise HarnessError('evidence_refs must be a list')
    now=clock or datetime.now(timezone.utc)
    if 'deadline_utc' not in state or 'finish_reserve_s' not in state:
        raise HarnessError('Declare deadline_utc and finish_reserve_s; no invented contest deadline')
    try:deadline=datetime.fromisoformat(state['deadline_utc'].replace('Z','+00:00'))
    except (TypeError,ValueError) as e:raise HarnessError('Invalid deadline_utc') from e
    if deadline.tzinfo is None:raise HarnessError('Deadline must have timezone')
    finish=state['finish_reserve_s']
    if not finite(finish) or finish<=0:raise HarnessError('Positive finish_reserve_s required')
    remaining=max(0.,(deadline-now).total_seconds())
    if state.get('remaining_research_s') is not None:
        r=state['remaining_research_s']
        if not finite(r) or r<0:raise HarnessError('Invalid remaining_research_s')
        remaining=min(remaining,r)
    stagnant=state.get('stagnant_trials',0)
    if type(stagnant)!=int or stagnant<0:raise HarnessError('stagnant_trials must be nonnegative integer')
    critical=state.get('critical_measurement_issue',False)
    if type(critical)!=bool:raise HarnessError('critical_measurement_issue must be boolean')
    if remaining<=finish:
        action='finalize_or_preserve';reason='Remaining delegated time does not exceed reserved verification and handoff time'
    elif not state['oracle_verified']:
        action='repair_evaluator';reason='Without a verified judge/adapter, improvement scores are not decision evidence'
    elif not state['baseline_valid']:
        action='establish_baseline';reason='Secure a legal, runnable candidate before exploring branches'
    elif critical:
        action='diagnose_evaluation';reason='An unresolved measurement mismatch can invalidate later comparisons'
    elif not state['evaluation_calibrated']:
        action='calibrate_measurement';reason='Estimate effect variability and test sensitivity before expensive screening'
    elif stagnant>=3:
        action='explore_alternative';reason='Repeated ineffective local refinements justify a bounded independent branch'
    else:
        action='exploit_or_test_hypothesis';reason='Validated comparison exists; continue a bounded high-value hypothesis'
    return {'recommended_action':action,'reason':reason,'remaining_effective_s':round(remaining,2),
      'finish_reserve_s':finish,'evidence_refs':state.get('evidence_refs',[]),
      'status':'advisory_only','caveats':['Needs a human/agent to verify the state and make a concrete experiment plan',
            'Does not start processes, use API, spend budget, change mission or adopt a candidate',
            'A replaceable deterministic prior, NOT a learned optimal meta-policy']}
