"""0.2.0 governance, deadline, and opt-in selector acceptance tests (offline)."""
import json, tempfile, unittest
from pathlib import Path
from datetime import datetime,timezone
from tests.helpers import make_project,mission
from harness import storage as S,exposure as H,precision as P,portfolio as D,experiments as X
from harness.util import HarnessError,save,load,sha

class ExposureGovernance(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.root=make_project(Path(self.t.name)/'project')
    def tearDown(self):self.t.cleanup()
    def test_cross_suite_same_bytes_refused(self):
        H.register(self.root,'run1','dev','suiteA','set1',['a'*64],['b1'])
        with self.assertRaises(HarnessError):H.preview(self.root,'audit',['a'*64])
        self.assertFalse(H.preview(self.root,'dev',['a'*64])['fresh'])
    def test_audit_reuse_after_copy_or_rename_refused(self):
        H.register(self.root,'r1','audit','suiteX','setX',['a'*64],['b1'],'human-ref')
        with self.assertRaises(HarnessError):H.register(self.root,'r2','audit','suiteY','setY',['a'*64],['b2'],'human-ref')
        with self.assertRaises(HarnessError):H.preview(self.root,'diagnostic',['a'*64])
        self.assertEqual(len(H.history(self.root)),1)
    def test_unseen_input_is_eligible(self):
        H.register(self.root,'r1','dev','s','cases',['1'*64],['b'])
        self.assertTrue(H.preview(self.root,'audit',['2'*64])['audit_eligible'])
    def test_corrupt_ledger_fail_closed(self):
        (S.base(self.root)/'exposures.jsonl').write_text('{not-json}\n')
        with self.assertRaises(HarnessError):H.preview(self.root,'audit',['a'*64])
    def test_duplicate_case_hash_is_rejected(self):
        with self.assertRaises(HarnessError):H.preview(self.root,'dev',['a'*64,'a'*64])

class PrecisionAndDecision(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.root=make_project(Path(self.t.name)/'project')
    def tearDown(self):self.t.cleanup()
    def pilot(self,values,purpose='dev',decision='inconclusive'):
        d=S.evidence(self.root,'runs')/'pilot';d.mkdir()
        save(d/'plan.json',{'purpose':purpose,'trials':[{'trial_id':str(i)} for i in range(2*len(values))]})
        save(d/'summary.json',{'decision':decision,'block_effects':{str(i):v for i,v in enumerate(values)},'ci95':[-.2,.2]})
        save(d/'state.json',{'state':'complete'})
    def test_precision_infeasible(self):
        self.pilot([-.2,.2,-.1,.1]);r=P.forecast(self.root,'pilot',.01,solver_run_budget=4)
        self.assertTrue(r['estimable']);self.assertFalse(r['within_additional_budget'])
    def test_precision_plenty_budget(self):
        self.pilot([.01,.02,.02,.03]);r=P.forecast(self.root,'pilot',.5,solver_run_budget=100)
        self.assertTrue(r['within_additional_budget'])
    def test_precision_requires_pilot(self):
        self.pilot([.1]);r=P.forecast(self.root,'pilot',.05)
        self.assertFalse(r['estimable'])
    def test_audit_is_not_precision_pilot(self):
        self.pilot([.1,.2],purpose='audit')
        with self.assertRaises(HarnessError):P.forecast(self.root,'pilot',.05)
    def test_outcome_has_independent_epistemic_status(self):
        e=X.init(self.root,{'hypothesis':'some hypothesis','work_id':'w','acceptance':'a','failure':'b','budget':{}})
        r=X.change(self.root,e['id'],result={'decision':'rejected','hypothesis_status':'unresolved','research_status':'stop'})
        self.assertEqual(r['result']['hypothesis_status'],'unresolved')
        self.assertEqual(r['result']['research_status'],'stop')
    def test_unknown_outcome_category_refused(self):
        e=X.init(self.root,{'hypothesis':'h','work_id':'w','acceptance':'a','failure':'b','budget':{}})
        with self.assertRaises(HarnessError):X.change(self.root,e['id'],result={'decision':'hold','hypothesis_status':'proven'})
    def state(self,**kw):
        s={'schema_version':1,'oracle_verified':True,'baseline_valid':True,'evaluation_calibrated':True,
           'deadline_utc':'2026-11-01T00:00:00+00:00','finish_reserve_s':3600,'stagnant_trials':0}
        s.update(kw);return s
    def clock(self):return datetime(2026,10,31,tzinfo=timezone.utc)
    def test_portfolio_baseline_oracle(self):
        self.assertEqual(D.recommend(self.state(oracle_verified=False),self.clock())['recommended_action'],'repair_evaluator')
        self.assertEqual(D.recommend(self.state(baseline_valid=False),self.clock())['recommended_action'],'establish_baseline')
    def test_portfolio_deadline_trumps_deep_exploration(self):
        close=datetime(2026,10,31,23,40,tzinfo=timezone.utc)
        self.assertEqual(D.recommend(self.state(stagnant_trials=10),close)['recommended_action'],'finalize_or_preserve')
    def test_portfolio_measurement_before_exploit(self):
        self.assertEqual(D.recommend(self.state(critical_measurement_issue=True),self.clock())['recommended_action'],'diagnose_evaluation')
        self.assertEqual(D.recommend(self.state(evaluation_calibrated=False),self.clock())['recommended_action'],'calibrate_measurement')
    def test_portfolio_stagnation(self):
        self.assertEqual(D.recommend(self.state(stagnant_trials=3),self.clock())['recommended_action'],'explore_alternative')
        self.assertEqual(D.recommend(self.state(),self.clock())['recommended_action'],'exploit_or_test_hypothesis')
    def test_portfolio_no_invented_deadline(self):
        with self.assertRaises(HarnessError):D.recommend({'schema_version':1,'baseline_valid':True,'oracle_verified':True,'evaluation_calibrated':True})

if __name__=='__main__':unittest.main()

class SearchIntegration(unittest.TestCase):
    def setUp(self):
        from tests.fixtures.setup import prepare
        from harness.artifacts import build
        self.t=tempfile.TemporaryDirectory(prefix='ahc-search-')
        self.root=make_project(Path(self.t.name)/'p',full=True)
        mission(self.root,{'solver_runs':25},{'evaluation':True})
        self.suite,self.s=prepare(self.root,'max',2)
        self.base=build(self.root,'tests/fixtures/selector.cpp','root-000')['artifact_id']
        # Independent source path produces a distinct, valid, frozen candidate with the same behavior.
        (self.root/'candidate.cpp').write_bytes((self.root/'tests/fixtures/selector.cpp').read_bytes())
        self.cand=build(self.root,'candidate.cpp','candidate-001')['artifact_id']
        from integrations.search import controller as C
        self.C=C
        self.plan={'schema_version':1,'search_id':'test-search','work_id':'work',
            'root_artifact_id':self.base,'selector':{'name':'greedy'},
            'actions':[{'name':'bounded-patch','definition':'modify the C++ solver within a small scope',
                        'generator_config_sha256':'a'*64}],
            'evaluation':{'suite':self.suite,'suite_sha256':sha(self.root/self.suite),
                          'baseline_artifact_id':self.base,'reward':{'kind':'logistic','tau':0.05}},
            'limits':{'max_trials':3,'max_pending':1}}
    def tearDown(self):self.t.cleanup()
    def test_complete_search_and_verified_reward(self):
        from harness import research as R
        from harness.evaluate import run_evaluation
        self.C.init(self.root,self.plan)
        trial=self.C.ask(self.root,'test-search')
        self.assertEqual(trial['parent_artifact_id'],self.base)
        with self.assertRaises(HarnessError):self.C.ask(self.root,'test-search')
        R.reserve(self.root,'work',['evaluation'],{'solver_runs':8})
        run=run_evaluation(self.root,self.suite,self.cand,self.base,'search-test',1,work='work')
        told=self.C.tell(self.root,'test-search',trial['trial_id'],self.cand,run['run_id'])
        self.assertTrue(0<=told['reward']<=1)
        self.assertTrue(self.C.tell(self.root,'test-search',trial['trial_id'],self.cand,run['run_id'])['already_told'])
        self.assertTrue(self.C.verify(self.root,'test-search')['valid'])
        next_trial=self.C.ask(self.root,'test-search')
        self.assertEqual(next_trial['parent_artifact_id'],self.cand)
        self.C.transition(self.root,'test-search',next_trial['trial_id'],'failed','compile problem')
        self.assertEqual(self.C.status(self.root,'test-search')['failed'],1)
        self.assertNotIn('submitted',S.read(self.root)['payload']['refs'])
    def test_corrupted_checkpoint_refused(self):
        self.C.init(self.root,self.plan)
        self.C.ask(self.root,'test-search')
        d=self.C.store(self.root,'test-search');s=load(d/'state.json')
        (d/s['checkpoint']['name']).write_text('tampered')
        with self.assertRaises(HarnessError):self.C.ask(self.root,'test-search')
    def test_changed_plan_refused(self):
        self.C.init(self.root,self.plan)
        d=self.C.store(self.root,'test-search');p=load(d/'plan.json');p['work_id']='swapped';save(d/'plan.json',p)
        with self.assertRaises(HarnessError):self.C.status(self.root,'test-search')
    def test_search_does_not_use_audit(self):
        self.s['purpose']='audit';save(self.root/self.suite,self.s)
        self.plan['evaluation']['suite_sha256']=sha(self.root/self.suite)
        with self.assertRaises(HarnessError):self.C.init(self.root,self.plan)
