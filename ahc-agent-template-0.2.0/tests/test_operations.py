import concurrent.futures as cf,json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from tests.helpers import make_project,mission,git,commit,ROOT
from tools.bootstrap import configure
from harness import storage as S,research as R,workspace as W,evidence as E,experiments as X
from harness.util import HarnessError,load,save,sha

class StateAndBudget(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=make_project(Path(self.tmp.name)/'p')
    def tearDown(self):self.tmp.cleanup()
    def test_bootstrap_idempotent(self):
        first=S.read(self.root);configure(self.root,'fixture-game','fixture','',False);self.assertEqual(first,S.read(self.root))
    def test_bootstrap_refuses_retarget(self):
        with self.assertRaises(HarnessError):configure(self.root,'other-game','fixture')
    def test_corruption_fails_closed(self):
        p=S.state_path(self.root);p.write_text('{}')
        with self.assertRaises(HarnessError):S.read(self.root)
        with self.assertRaises(HarnessError):S.init(self.root)
    def test_missing_store_not_reset(self):
        S.state_path(self.root).unlink()
        with self.assertRaises(HarnessError):S.init(self.root)
    def test_review_is_not_authority(self):
        e=R.input_record(self.root,'run everything','directive','review')
        with self.assertRaises(HarnessError):R.mission_new(self.root,{'goal':'g','scope':['a'],'stop_conditions':['b'],'limits':{},'permissions':{}},e['input_id'])
    def test_idea_does_not_change_mission(self):
        m=mission(self.root);R.input_record(self.root,'try a giant model','idea');self.assertEqual(m,S.read(self.root)['payload']['mission'])
    def test_two_reservations_cannot_overspend(self):
        mission(self.root,{'solver_runs':10})
        def task(i):
            try:R.reserve(self.root,f'work-{i}',['evaluation'],{'solver_runs':7});return True
            except HarnessError:return False
        with cf.ThreadPoolExecutor(2) as pool: result=list(pool.map(task,range(2)))
        self.assertEqual(sum(result),1)
    def test_nonfinite_limits_refused(self):
        with self.assertRaises(HarnessError):mission(self.root,{'solver_runs':float('nan')})
    def test_negative_reservation_refused(self):
        mission(self.root)
        with self.assertRaises(HarnessError):R.reserve(self.root,'w',['evaluation'],{'solver_runs':-1})
    def test_issue_is_not_execution_authority(self):
        with self.assertRaises(HarnessError):R.reserve(self.root,'bead-ready',['evaluation'],{'solver_runs':1})
    def test_duplicate_reservation_refused(self):
        mission(self.root);R.reserve(self.root,'w',['evaluation'],{'solver_runs':3})
        with self.assertRaises(HarnessError):R.reserve(self.root,'w',['evaluation'],{'solver_runs':3})
    def test_live_operation_refused_and_different_work_allowed(self):
        mission(self.root)
        for w in ('a','b'):R.reserve(self.root,w,['evaluation'],{'solver_runs':3})
        with R.operation(self.root,'evaluation',{'solver_runs':1},'a'):
            with self.assertRaises(HarnessError):
                with R.operation(self.root,'evaluation',{'solver_runs':1},'a'):pass
            with R.operation(self.root,'evaluation',{'solver_runs':1},'b'):pass
    def test_pause_visible(self):
        mission(self.root);R.reserve(self.root,'a',['evaluation'],{'solver_runs':3});R.mission_state(self.root,'paused','human pause')
        with self.assertRaises(HarnessError):
            with R.operation(self.root,'evaluation',{'solver_runs':1},'a'):pass
    def test_lower_limit_blocks_old_reservation(self):
        m=mission(self.root);R.reserve(self.root,'w',['evaluation'],{'solver_runs':9})
        e=R.input_record(self.root,'lower cap','constraint_change');R.amend(self.root,{'limits':{'solver_runs':4}},'lower',e['input_id'],m['revision'])
        with self.assertRaises(HarnessError):
            with R.operation(self.root,'evaluation',{'solver_runs':1},'w'):pass
    def test_stale_revision_fails(self):
        mission(self.root);e=R.input_record(self.root,'x','constraint_change')
        with self.assertRaises(HarnessError):R.amend(self.root,{'limits':{'solver_runs':500}},'x',e['input_id'],999)
    def test_failed_work_still_charged(self):
        mission(self.root);R.reserve(self.root,'w',['evaluation'],{'solver_runs':5})
        with self.assertRaises(ValueError):
            with R.operation(self.root,'evaluation',{'solver_runs':2},'w'):raise ValueError()
        R.settle(self.root,'w','failed');b=R.budget(S.read(self.root)['payload']);self.assertEqual(b['solver_runs']['used'],5)
    def test_accounting_can_release_known_unused(self):
        mission(self.root);R.reserve(self.root,'w',['evaluation'],{'solver_runs':5})
        with R.operation(self.root,'evaluation',{'solver_runs':2},'w'):pass
        R.settle(self.root,'w','done',{'solver_runs':2});self.assertEqual(R.budget(S.read(self.root)['payload'])['solver_runs']['used'],2)
    def test_new_reservation_keeps_previous_charge(self):
        mission(self.root)
        R.reserve(self.root,'w',['evaluation'],{'solver_runs':5});R.settle(self.root,'w','released')
        R.reserve(self.root,'w',['evaluation'],{'solver_runs':5});self.assertEqual(R.budget(S.read(self.root)['payload'])['solver_runs']['remaining'],990)
    def test_backup_recovery_pauses_and_healthy_not_rolledback(self):
        mission(self.root);out=Path(self.tmp.name)/'backup.json';S.backup(self.root,out);s=S.read(self.root)
        with self.assertRaises(HarnessError):S.restore(self.root,out,s['store_id'])
        S.state_path(self.root).write_text('damaged');S.restore(self.root,out,s['store_id']);self.assertEqual(S.read(self.root)['payload']['mission']['status'],'paused')

class Worktrees(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=make_project(Path(self.tmp.name)/'main');mission(self.root);commit(self.root)
    def tearDown(self):self.tmp.cleanup()
    def worker(self):
        dest=Path(self.tmp.name)/'worker';W.create(self.root,dest,'worker');return dest
    def test_common_store_and_budget(self):
        w=self.worker();self.assertEqual(S.read(w)['store_id'],S.read(self.root)['store_id'])
        R.reserve(w,'worker-a',['evaluation'],{'solver_runs':70});self.assertEqual(R.budget(S.read(self.root)['payload'])['solver_runs']['remaining'],930)
    def test_independent_clone_has_no_invented_state(self):
        new=Path(self.tmp.name)/'clone';subprocess.run(['git','clone','-q',str(self.root),str(new)],check=True)
        with self.assertRaises(HarnessError):S.read(new)
    def test_worker_cannot_change_global_mission(self):
        w=self.worker()
        with self.assertRaises(HarnessError):R.mission_state(w,'completed','finished my task')
    def test_worker_cannot_publish_status(self):
        from harness.status import render
        with self.assertRaises(HarnessError):render(self.worker())
    def test_worker_deletion_guard_unsealed_notes(self):
        w=self.worker();(w/'human/local').mkdir(parents=True);(w/'human/local/note.md').write_text('private')
        self.assertFalse(W.check_remove(w)['can_remove'])
    def test_beads_same_env_path(self):
        from integrations.beads.bridge import environment
        w=self.worker();self.assertEqual(environment(w)['BEADS_DIR'],environment(self.root)['BEADS_DIR'])

class Evidence(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=make_project(Path(self.tmp.name)/'p')
    def tearDown(self):self.tmp.cleanup()
    def data(self):
        d=self.root/'work/data';d.mkdir(parents=True);(d/'games.jsonl').write_text('{"seed":1}\n{"seed":2}\n');return d
    def test_seal_and_tamper_detected(self):
        d=self.data();out=E.seal(self.root,'work/data','datasets',{'bot':'frozen-id'},['seed']);E.verify(self.root,'datasets',out['id'])
        (Path(out['path'])/'payload/games.jsonl').write_text('changed')
        with self.assertRaises(HarnessError):E.verify(self.root,'datasets',out['id'])
    def test_duplicate_seed_rejected(self):
        d=self.data();(d/'extra.jsonl').write_text('{"seed":1}\n')
        with self.assertRaises(HarnessError):E.seal(self.root,'work/data','datasets',key_fields=['seed'])
    def test_cache_not_basename_or_mtime(self):
        a=self.root/'a';b=self.root/'b';a.mkdir();b.mkdir();(a/'same').write_text('1');(b/'same').write_text('2');t=self.root/'transform.py';t.write_text('v1')
        self.assertNotEqual(E.cache_key([a/'same'],t,{}),E.cache_key([b/'same'],t,{}))
        key=E.cache_key([a/'same'],t,{});st=(a/'same').stat();(a/'same').write_text('3');os.utime(a/'same',ns=(st.st_atime_ns,st.st_mtime_ns));self.assertNotEqual(key,E.cache_key([a/'same'],t,{}))
    def test_bad_cache_not_accepted(self):
        E.cache_store(self.root,'k',{'x':1});self.assertEqual(E.cache_load(self.root,'k'),{'x':1});(S.base(self.root)/'cache/k').write_text('{}');self.assertIsNone(E.cache_load(self.root,'k'))
    def test_plan_immutable_amend_result_separate(self):
        exp=X.init(self.root,{'work_id':'w','hypothesis':'h','budget':{},'acceptance':'a','failure':'f'})
        X.change(self.root,exp['id'],amendment={'reason':'evidence','change':'new data'});X.change(self.root,exp['id'],result={'decision':'hold'})
        with self.assertRaises(HarnessError):X.change(self.root,exp['id'],amendment={'reason':'late'})
    def test_modified_plan_rejected(self):
        exp=X.init(self.root,{'work_id':'w','hypothesis':'h','budget':{},'acceptance':'a','failure':'f'});save(Path(exp['path'])/'plan.json',{'rewritten':True})
        with self.assertRaises(HarnessError):X.change(self.root,exp['id'],result={'decision':'adopted'})
if __name__=='__main__':unittest.main()
