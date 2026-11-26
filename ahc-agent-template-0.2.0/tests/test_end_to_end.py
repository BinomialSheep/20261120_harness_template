import json,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from tests.helpers import make_project,mission,commit,git,ROOT
from tests.fixtures.setup import prepare
from harness.artifacts import build,resolve,adopt
from harness.evaluate import run_evaluation
from harness.replay import replay
from harness.judge import score_output
from harness import storage as S,research as R,workspace as W,cases as C
from harness.util import load,save,HarnessError,sha

class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='ahc-test ');self.root=make_project(Path(self.tmp.name)/'project',full=True)
        mission(self.root,{'solver_runs':1000,'judge_runs':10,'generated_cases':20},{'evaluation':True,'rescore':True,'case_generation':True,'local_adopt':True})
    def tearDown(self):self.tmp.cleanup()
    def run_fixture(self,kind='max',n=2,source=None):
        path,s=prepare(self.root,kind,n)
        src=source or ('tests/fixtures/search.cpp' if kind=='interactive' else 'tests/fixtures/selector.cpp')
        a=build(self.root,src,'candidate');b=build(self.root,'tests/fixtures/selector.cpp' if kind!='interactive' else 'tests/fixtures/search.cpp','base')
        R.reserve(self.root,'work',['evaluation'],{'solver_runs':100})
        out=run_evaluation(self.root,path,a['artifact_id'],b['artifact_id'],'test',2,work='work')
        return out,s
    def test_max_batch_and_replay(self):
        out,s=self.run_fixture();self.assertEqual(out['summary']['status_counts'],{'ok':4});self.assertGreater(out['summary']['mean_effect'],0)
        r=replay(self.root,out['run_id'],'t0000000','work');self.assertTrue(r['same_outcome']);self.assertTrue(r['output_sha_equal'])
    def test_min_batch(self):
        out,s=self.run_fixture('min');self.assertGreater(out['summary']['mean_effect'],0)
    def test_interactive_no_hidden_input_and_closed_loop_rerun(self):
        out,s=self.run_fixture('interactive');self.assertEqual(out['summary']['status_counts'],{'ok':4})
        self.assertGreater(out['summary']['mean_effect'],0)
        rd=S.evidence(self.root,'runs')/out['run_id'];trace=[json.loads(l) for l in (rd/'trials/t0000000/transcript.jsonl').read_text().splitlines()]
        self.assertEqual(trace[0]['text'],'SEARCH_V1 0 255 512')
        r=replay(self.root,out['run_id'],'t0000000','work');self.assertTrue(r['same_outcome']);self.assertTrue(r['transcript_sha_equal'])
    def test_invalid_not_rewarded(self):
        (self.root/'invalid.cpp').write_text('#include <iostream>\nint main(){std::cout<<"0 0 0\\n";}')
        out,s=self.run_fixture(source='invalid.cpp');self.assertEqual(out['summary']['decision'],'invalid')
        self.assertEqual(out['summary']['status_by_role']['candidate'],{'solver_invalid':2})
    def test_timeout_and_crash(self):
        path,s=prepare(self.root,'max',1);c=load(self.root/s['contract']);c['limits']['solver_wall_s']=.2;save(self.root/s['contract'],c)
        (self.root/'bad.cpp').write_text('#include <chrono>\n#include <thread>\nint main(){std::this_thread::sleep_for(std::chrono::seconds(3));}')
        build(self.root,'bad.cpp','slow');build(self.root,'tests/fixtures/selector.cpp','base')
        R.reserve(self.root,'w',['evaluation'],{'solver_runs':4})
        out=run_evaluation(self.root,path,'slow','base','tle',1,work='w')
        self.assertEqual(out['summary']['status_by_role']['candidate'],{'solver_timeout':1})
        (self.root/'bad.cpp').write_text('int main(){return 7;}');build(self.root,'bad.cpp','crash')
        out=run_evaluation(self.root,path,'crash','base','re',1,work='w')
        self.assertEqual(out['summary']['status_by_role']['candidate'],{'solver_crash':1})
    def test_output_limit(self):
        path,s=prepare(self.root,'max',1);c=load(self.root/s['contract']);c['limits']['max_output_bytes']=100;save(self.root/s['contract'],c)
        (self.root/'spam.cpp').write_text('#include <iostream>\nint main(){for(int i=0;i<10000;++i)std::cout<<"1234 ";}')
        build(self.root,'spam.cpp','spam');build(self.root,'tests/fixtures/selector.cpp','base');R.reserve(self.root,'w',['evaluation'],{'solver_runs':2})
        o=run_evaluation(self.root,path,'spam','base','overflow',1,work='w')
        self.assertEqual(o['summary']['status_by_role']['candidate'],{'solver_output_limit':1})
    def test_output_rejudge(self):
        path,s=prepare(self.root,'max',1);p=self.root/'answer.txt';p.write_text('0 1 2\n');R.reserve(self.root,'j',['rescore'],{'judge_runs':2})
        r=score_output(self.root,s['case_set_id'],'c0000',p,s['contract'],'j');self.assertEqual(r['result']['status'],'ok')
        p.write_text('0 0 0');r=score_output(self.root,s['case_set_id'],'c0000',p,s['contract'],'j');self.assertEqual(r['result']['status'],'solver_invalid')
    def test_oracle_tamper_blocks_replay(self):
        o,s=self.run_fixture();rd=S.evidence(self.root,'runs')/o['run_id'];(rd/'oracle_snapshot/tests/fixtures/judge.py').write_text('print(999999)')
        with self.assertRaises(HarnessError):replay(self.root,o['run_id'],'t0000000','work')
    def test_aa_and_repeats(self):
        path,s=prepare(self.root,'max',2);s['variants']={};s['solver_seeds']=[0,1,2];save(self.root/path,s)
        a=build(self.root,'tests/fixtures/selector.cpp','same');R.reserve(self.root,'w',['evaluation'],{'solver_runs':12})
        o=run_evaluation(self.root,path,'same','same','aa',2,work='w');self.assertEqual(o['summary']['mean_effect'],0);self.assertEqual(o['summary']['n_blocks'],2)
    def test_seed_and_parameters_are_received(self):
        path,s=prepare(self.root,'max',1)
        (self.root/'seed.cpp').write_text('#include <cstdlib>\n#include <fstream>\n#include <iostream>\nint main(){std::cerr<<std::getenv("AHC_SOLVER_SEED");std::cout<<"0 1 2\\n";}')
        s['solver_seeds']=[47];s['variants']={};save(self.root/path,s);build(self.root,'seed.cpp','seed');R.reserve(self.root,'w',['evaluation'],{'solver_runs':2})
        o=run_evaluation(self.root,path,'seed','seed','seed',1,work='w');rd=S.evidence(self.root,'runs')/o['run_id'];self.assertEqual((rd/'trials/t0000000/solver.err').read_text(),'47')
    def test_visualize(self):
        from tools.visualize import visualize
        o,s=self.run_fixture();result=visualize(self.root,o['run_id'],'t0000000',True);self.assertTrue(result['ok'])
        self.assertTrue((Path(result['directory'])/'view.svg').is_file())
    def test_generator_snapshot(self):
        R.reserve(self.root,'g',['case_generation'],{'generated_cases':3})
        r=C.generate(self.root,{'schema_version':1,'files':['tests/fixtures/generator.py'],
             'command':['{python}','{oracle}/tests/fixtures/generator.py','{seed}'],'seeds':[12,13,14]},'g')
        self.assertEqual(len(C.resolve(self.root,r['case_set_id'])['identity']['cases']),3)
    def test_case_tamper_detected(self):
        _,s=prepare(self.root,'max',2);m=C.resolve(self.root,s['case_set_id']);(Path(m['directory'])/'inputs/c0000.in').write_text('bad')
        with self.assertRaises(HarnessError):C.resolve(self.root,s['case_set_id'])
    def test_duplicate_case_not_independent(self):
        p=self.root/'x';p.write_text('1\n')
        with self.assertRaises(HarnessError):C.freeze(self.root,{'schema_version':1,'cases':[{'id':'a','path':'x'},{'id':'b','path':'x'}]})
    def test_frozen_case_and_artifact_survive_worker_removal(self):
        commit(self.root);w=Path(self.tmp.name)/'worker';W.create(self.root,w,'worker')
        path,s=prepare(w,'max',2);a=build(w,'tests/fixtures/selector.cpp','worker-solver');R.reserve(w,'w',['evaluation'],{'solver_runs':5})
        o=run_evaluation(w,path,a['artifact_id'],a['artifact_id'],'worker',1,work='w')
        shutil.rmtree(w/'work');subprocess.run(['git','worktree','remove',str(w)],cwd=self.root,check=True,capture_output=True)
        r=replay(self.root,o['run_id'],'t0000000','w');self.assertTrue(r['same_outcome'])
    def test_adoption_compare_and_swap_and_worker(self):
        a=build(self.root,'tests/fixtures/selector.cpp','candidate');adopt(self.root,'best_local',a['artifact_id'],None,'measured',['run:test'])
        with self.assertRaises(HarnessError):adopt(self.root,'best_local',a['artifact_id'],None,'stale',['run:test'])
        commit(self.root);w=Path(self.tmp.name)/'w';W.create(self.root,w,'w')
        with self.assertRaises(HarnessError):adopt(w,'best_local',a['artifact_id'],a['artifact_id'],'worker',['run:test'])
    def test_production_stub_does_not_break_fixtures(self):
        build(self.root,'tests/fixtures/selector.cpp','fixture')
        with self.assertRaises(HarnessError):build(self.root,'solvers/000_baseline.cpp','unfinished')
    def test_frozen_headers(self):
        (self.root/'h.hpp').write_text('constexpr int x=1;\n');(self.root/'m.cpp').write_text('#include "h.hpp"\nint main(){return x-1;}\n')
        a=build(self.root,'m.cpp','a');b=build(self.root,'m.cpp','b');self.assertEqual(a['artifact_id'],b['artifact_id'])
        (self.root/'h.hpp').write_text('constexpr int x=2;\n');m=resolve(self.root,'a');self.assertIn('x=1',(Path(m['directory'])/'snapshot/h.hpp').read_text())
        (Path(m['directory'])/'snapshot/h.hpp').write_text('bad')
        with self.assertRaises(HarnessError):resolve(self.root,'a')
if __name__=='__main__':unittest.main()
