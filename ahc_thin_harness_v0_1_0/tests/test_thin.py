import hashlib,json,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from thin import core as C,scoring as E
from thin.config import load as config_load
from thin.cli import demo
from thin.process import execute
from thin.util import ThinError,read_json,write_json,digest

class ThinHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='AHC thin - tests ')
        cls.root=Path(cls.tmp.name)/'toy workspace'
        cls.demo_result=demo(cls.root)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_end_to_end_10_parallel_candidates(self):
        d=self.demo_result
        self.assertEqual(d['workers'],10)
        self.assertEqual(d['candidate_count'],10)
        self.assertEqual(d['valid_count'],10)
        self.assertTrue(d['promoted'])
        self.assertEqual(d['best']['job'],'A01')
    def test_isolated_worker_has_no_research_history(self):
        w=self.root/'runs/R0001/jobs/A02/workspace'
        self.assertTrue((w/'task.md').exists());self.assertTrue((w/'problem/statement.md').exists())
        self.assertTrue((w/'parent_1.cpp').exists());self.assertFalse((w/'runs').exists())
        self.assertFalse((w/'best').exists());self.assertFalse((w/'experiments').exists())
        self.assertFalse((w/'results.json').exists())
        self.assertNotIn('1214.8',(w/'task.md').read_text())
        self.assertTrue((w/'.git').is_dir())
    def test_single_evaluation_reused_for_identical_codes(self):
        rd=self.root/'runs/R0001/evaluation'
        self.assertTrue((rd/'A01/scores.csv').exists())
        self.assertFalse((rd/'A02/scores.csv').exists())
        result=read_json(self.root/'runs/R0001/result.json')
        self.assertTrue(all(r['valid'] for r in result['candidates']))
        self.assertEqual(len({r['source_sha256'] for r in result['candidates']}),1)
    def test_seed0_gate_and_fresh_round_evaluation_seeds(self):
        rd=self.root/'runs/R0001/evaluation/A01'
        self.assertEqual(read_json(rd/'seed0.json')['status'],'ok')
        with (rd/'scores.csv').open() as f:
            rows=f.read().splitlines()
        self.assertIn('1000,',rows[1]);self.assertIn('1004,',rows[-1]);self.assertEqual(len(rows),6)
    def test_quickcheck_only_seed0_A(self):
        w=self.root/'runs/R0001/jobs/A01/workspace'
        a=subprocess.run([sys.executable,'quickcheck.py','--seed','1000'],cwd=w,text=True,capture_output=True,timeout=10)
        self.assertNotEqual(a.returncode,0)
        b=subprocess.run([sys.executable,'quickcheck.py','--seed','0'],cwd=w,text=True,capture_output=True,timeout=30)
        self.assertEqual(b.returncode,0,b.stderr)
    def test_re_evaluate_does_not_duplicate_or_modify_scores(self):
        rd=self.root/'runs/R0001';saved=digest(rd/'evaluation/A01/scores.csv')
        res=E.evaluate(self.root,'R0001')
        self.assertEqual(res['recommended'],'A01')
        self.assertEqual(saved,digest(rd/'evaluation/A01/scores.csv'))
    def test_second_round_B_uses_current_best_and_fresh_seeds(self):
        # New temp toy workspace: keep this independent of other tests.
        with tempfile.TemporaryDirectory(prefix='AHC B test ') as td:
            r=Path(td)/'w';shutil.copytree(self.root,r)
            p=C.prepare(r,'B',idea_text='Rewrite the critical step to reduce worst-case complexity without sacrificing validity')
            self.assertEqual(p['workers'],1);self.assertEqual(p['seed_start'],1005)
            w=r/'runs/R0002/jobs/B01/workspace'
            self.assertEqual((w/'parent_1.cpp').read_bytes(),(r/'best/main.cpp').read_bytes())
            job=C.run_workers(r,'R0002','demo')
            self.assertEqual(job['workers'][0]['status'],'submitted')
            results=E.evaluate(r,'R0002')
            self.assertEqual(results['seed_count'],5);self.assertEqual(results['candidates'][0]['effect'],0)
            with self.assertRaises(ThinError):E.promote(r,'R0002','B01')
            self.assertEqual(C.close(r,'R0002')['phase'],'closed')
    def test_modified_case_tool_or_candidate_refused(self):
        with tempfile.TemporaryDirectory() as td:
            r=Path(td)/'w';shutil.copytree(self.root,r)
            p=C.prepare(r,'B',idea_text='Check the impact of a minor structural change with fixed evaluation conditions')
            C.run_workers(r,'R0002','demo')
            (r/'runs/R0002/judge_tools/score_case.py').write_text('malicious evaluator')
            with self.assertRaises(ThinError):E.evaluate(r,'R0002')
    def test_idea_contract_rejects_shortcuts(self):
        ideas=read_json(self.root/'ideas.json')
        with tempfile.TemporaryDirectory() as td:
            q=Path(td)/'ideas.json';write_json(q,{'ideas':ideas['ideas'][:9]})
            with self.assertRaises(ThinError):C._ideas('A',q,None)
            for x in ideas['ideas']:x['idea']='Same repeated idea across all branches'
            write_json(q,ideas)
            with self.assertRaises(ThinError):C._ideas('A',q,None)
    def test_manual_invalid_worker_does_not_displace_best(self):
        with tempfile.TemporaryDirectory(prefix='AHC bad worker ') as td:
            r=Path(td)/'w';shutil.copytree(self.root,r)
            C.prepare(r,'B',idea_text='Attempt a standalone novel strategy and verify failure behavior')
            w=r/'runs/R0002/jobs/B01/workspace'
            (w/'main.cpp').write_text('#include <iostream>\nint main(){return 1;}\n')
            C.submit(r,'R0002','B01')
            C.mark_workers_done(r,'R0002')
            results=E.evaluate(r,'R0002')
            self.assertFalse(results['candidates'][0]['valid'])
            self.assertEqual(results['candidates'][0]['status'],'seed0_failed')
            with self.assertRaises(ThinError):E.promote(r,'R0002','B01')
            self.assertEqual(C.read_best(r)['job'],'A01')
            C.close(r,'R0002')
    def test_new_round_without_initial_best_is_possible(self):
        with tempfile.TemporaryDirectory(prefix='AHC fresh ') as td:
            r=Path(td)/'fresh';shutil.copytree(ROOT/'thin',r/'thin')
            (r/'problem/tools').mkdir(parents=True)
            shutil.copyfile(ROOT/'examples/toy/statement.md',r/'problem/statement.md')
            shutil.copyfile(ROOT/'examples/toy/tools/score_case.py',r/'problem/tools/score_case.py')
            shutil.copyfile(ROOT/'examples/toy/config.json',r/'config.json')
            shutil.copytree(ROOT/'scripts',r/'scripts')
            ideas={'ideas':[{'kind':'new','idea':f'Independent first implementation with technique number {i}', 'parents':[]} for i in range(10)]}
            write_json(r/'ideas.json',ideas)
            C.prepare(r,'A',ideas_path=r/'ideas.json')
            w=r/'runs/R0001/jobs/A01/workspace'
            shutil.copyfile(ROOT/'examples/toy/optimized.cpp',w/'main.cpp')
            C.submit(r,'R0001','A01')
            for i in range(2,11):C.fail_job(r,'R0001',f'A{i:02d}','not attempted in small test')
            C.mark_workers_done(r,'R0001')
            result=E.evaluate(r,'R0001')
            self.assertIsNone(result['baseline'])
            self.assertEqual(result['recommended'],'A01')
            C_state=C.read_best(r)
            self.assertIsNone(C_state)
            E.promote(r,'R0001','A01')
            self.assertIsNotNone(C.read_best(r))
            C.close(r,'R0001')
    def test_invalid_score_is_not_zero(self):
        a={'judge':{'direction':'maximize','metric':'paired_difference'}}
        with self.assertRaises(KeyError):E.compare({0:3.},{1:5.},a,[0])
    def test_kill_on_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            p=execute([sys.executable,'-c','import time;time.sleep(10)'],td,.05)
            self.assertEqual(p['status'],'timeout')

class ThinConfig(unittest.TestCase):
    def test_example_is_1000_and_20_120(self):
        c=read_json(ROOT/'config.example.json')
        self.assertEqual(c['evaluation']['count'],1000)
        self.assertEqual(c['workers']['minutes_A'],20)
        self.assertEqual(c['workers']['minutes_B'],120)
        self.assertEqual(c['workers']['parallelism_A'],10)
        self.assertEqual(c['workers']['debug_seeds_A'],[0])
    def test_non_shell_command(self):
        c=read_json(ROOT/'config.example.json')
        self.assertEqual(c['solver']['build'][0],'g++')
        self.assertEqual(c['workers']['engines']['codex'][-1],'-')
    def test_no_old_heavy_harness(self):
        for p in ('harness','integrations/beads','research/mission.json','AGENTS.old.md'):
            self.assertFalse((ROOT/p).exists())

if __name__=='__main__':unittest.main()
