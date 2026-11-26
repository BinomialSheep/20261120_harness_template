"""Pre-flight precision forecast from a *pilot* run, not a post-hoc proof of improvement.

Uses between-block sample SD and a normal approximation. It only suggests order of magnitude:
heterogeneous blocks, adaptive selection, repeated checking and score distribution shifts can
make this estimate inappropriate. Never use an audit run for planning a new candidate search.
"""
from __future__ import annotations
import math,statistics
from . import storage as S
from .util import HarnessError,finite,load

def forecast(root,run_id,target_halfwidth,solver_run_budget=None,candidates=1):
    if not finite(target_halfwidth) or target_halfwidth<=0:raise HarnessError('target half-width must be positive and finite')
    if solver_run_budget is not None and (type(solver_run_budget)!=int or solver_run_budget<0):
        raise HarnessError('solver run budget must be a nonnegative integer')
    if type(candidates)!=int or candidates<1:raise HarnessError('candidate count must be a positive integer')
    rd=S.evidence(root,'runs')/run_id
    plan=load(rd/'plan.json');summary=load(rd/'summary.json');state=load(rd/'state.json')
    if state.get('state')!='complete' or summary.get('decision')=='invalid':
        return {'estimable':False,'reason':'pilot evaluation was incomplete or invalid'}
    if plan.get('purpose')=='audit':raise HarnessError('Do not tune precision using protected audit results')
    blocks=list(summary.get('block_effects',{}).values())
    n=len(blocks)
    if n<2 or any(not finite(x) for x in blocks):
        return {'estimable':False,'reason':'need at least two independent finite block effects'}
    sd=statistics.stdev(blocks)
    required=max(2,math.ceil((1.96*sd/target_halfwidth)**2))
    per_block=plan['trials'].__len__()/n
    # Exact number only if the pilot has a constant number of solver runs per block.
    constant=per_block.is_integer()
    per_block=int(per_block) if constant else per_block
    total=math.ceil(required*per_block*candidates)
    remaining=max(0,math.ceil((required-n)*per_block*candidates))
    possible=solver_run_budget is None or remaining<=solver_run_budget
    return {'estimable':True,'pilot_run_id':run_id,'n_pilot_blocks':n,'block_sd':sd,
      'pilot_ci95':summary.get('ci95'),'target_halfwidth':target_halfwidth,
      'approx_required_blocks_per_candidate':required,'solver_runs_per_block':per_block,
      'estimated_additional_solver_runs':remaining,'estimated_total_solver_runs':total,
      'proposed_candidates':candidates,'within_additional_budget':possible,
      'decision':'calibrate_or_change_experiment' if not possible else 'potentially_feasible',
      'assumptions':['normal approximation using sample SD of equal-weight block means',
        'pilot cases and future cases are representative and comparably variable',
        'not a power analysis, not confidence-interval coverage under repeated selection',
        'case_mean with heterogeneous block sizes/weights requires a separate design check'],
      'suggestion':'If infeasible, target a larger effect, change the estimator/case mix, or move to another research action.'}
