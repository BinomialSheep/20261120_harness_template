import copy,json,math,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness.util import HarnessError,safe_name,finite,digest,save,load,contained,sha
from harness.process import run,clean_env
from harness.stats import summarize,effect
from harness.evaluate import validate_suite,validate_response
from harness.bundle import expand,code_mask
from harness.interactive import LineBot,BotFault
ROOT=Path(__file__).resolve().parents[1]

def fake_plan(n=24,repeats=2):
    p={'metric':{'direction':'maximize','comparison':'difference','aggregation':'case_mean'},'minimum_blocks':20,
       'cases':[{'id':str(i),'block':str(i),'weight':1,'tags':{}} for i in range(n)],'trials':[]}
    rows=[]
    for i in range(n):
        for rep in range(repeats):
            for role,value in [('candidate',2),('baseline',1)]:
                tid=str(len(rows));p['trials'].append(dict(trial_id=tid,case_id=str(i),repeat=rep,solver_seed=rep,role=role))
                rows.append(dict(trial_id=tid,status='ok',valid=True,raw_score=value))
    return p,rows

def fake_suite():
    return {'schema_version':1,'mode':'ahc','purpose':'dev','contract':'problem/contract.json','case_set_id':'a'*64,
            'metric':{'direction':'maximize','comparison':'difference'},'solver_seeds':[0,1]}

class Validation(unittest.TestCase):
    def test_finite(self):
        for x in [True,None,float('inf'),float('nan'),'1']:self.assertFalse(finite(x))
        self.assertTrue(finite(0));self.assertTrue(finite(-3.5))
    def test_names(self):
        for s in ['../x','a/b','x y','', 'a'*129]:
            with self.assertRaises(HarnessError):safe_name(s)
    def test_hash_order(self):self.assertEqual(digest({'a':1,'b':2}),digest({'b':2,'a':1}))
    def test_path_escape(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(HarnessError):contained(Path(d),'../other',exists=False)
    def test_response_nan(self):
        with self.assertRaises(HarnessError):validate_response({'schema_version':1,'status':'ok','valid':True,'raw_score':float('nan')})
    def test_response_unknown_status(self):
        with self.assertRaises(HarnessError):validate_response({'schema_version':1,'status':'WIN','raw_score':1})
    def test_failure_cannot_be_zero_cost(self):
        with self.assertRaises(HarnessError):validate_response({'schema_version':1,'status':'solver_invalid','valid':False,'raw_score':0})
    def test_valid_failure(self):validate_response({'schema_version':1,'status':'solver_timeout','valid':False,'raw_score':None})
    def test_valid_zero_and_negative(self):
        for v in (0,-5):validate_response({'schema_version':1,'status':'ok','valid':True,'raw_score':v})
    def test_suite(self):validate_suite(fake_suite())
    def test_duplicate_seed(self):
        s=fake_suite();s['solver_seeds']=[0,0]
        with self.assertRaises(HarnessError):validate_suite(s)
    def test_unknown_suite_option(self):
        s=fake_suite();s['opponents']=[]
        with self.assertRaises(HarnessError):validate_suite(s)
    def test_variant_environment_not_secret(self):
        s=fake_suite();s['variants']={'candidate':{'env':{'API_TOKEN':'x'}}}
        with self.assertRaises(HarnessError):validate_suite(s)
    def test_direction(self):
        self.assertEqual(effect(5,7,{'direction':'minimize','comparison':'difference'}),2)
        self.assertAlmostEqual(effect(1,2,{'direction':'minimize','comparison':'log_ratio'}),math.log(2))
    def test_log_zero(self):
        with self.assertRaises(HarnessError):effect(0,1,{'direction':'minimize','comparison':'log_ratio'})

class Statistics(unittest.TestCase):
    def test_positive(self):
        p,r=fake_plan();s=summarize(r,p,resamples=100)
        self.assertEqual(s['decision'],'positive_evidence');self.assertEqual(s['mean_effect'],1)
    def test_aa(self):
        p,r=fake_plan()
        for x in r:x['raw_score']=1
        s=summarize(r,p,resamples=100);self.assertEqual(s['ci95'],[0,0]);self.assertEqual(s['decision'],'inconclusive')
    def test_repeats_not_independent_inputs(self):
        p,r=fake_plan(3,10);s=summarize(r,p,resamples=100);self.assertEqual(s['n_blocks'],3);self.assertEqual(s['planned_trials'],60)
    def test_missing(self):
        p,r=fake_plan();s=summarize(r[:-1],p,resamples=100);self.assertEqual(s['decision'],'invalid')
    def test_duplicates(self):
        p,r=fake_plan();self.assertEqual(summarize(r+[r[0]],p,resamples=100)['decision'],'invalid')
    def test_failure_not_dropped(self):
        p,r=fake_plan();r[0].update(status='solver_invalid',valid=False,raw_score=None)
        s=summarize(r,p,resamples=100);self.assertEqual(s['decision'],'invalid');self.assertIsNone(s['mean_effect'])
        self.assertEqual(s['invalid_cases'],['0']);self.assertEqual(s['status_by_role']['candidate']['solver_invalid'],1)
    def test_low_n(self):
        p,r=fake_plan(3);self.assertEqual(summarize(r,p,resamples=100)['decision'],'insufficient_blocks')
    def test_zero_valid(self):
        p,r=fake_plan()
        for x in r:x['raw_score']=0
        self.assertEqual(summarize(r,p,resamples=100)['mean_effect'],0)
    def test_log_invalid(self):
        p,r=fake_plan();r[0]['raw_score']=0;p['metric']['comparison']='log_ratio'
        self.assertEqual(summarize(r,p,resamples=100)['decision'],'invalid')
    def test_minimization_sign(self):
        p,r=fake_plan();p['metric']['direction']='minimize'
        self.assertEqual(summarize(r,p,resamples=100)['decision'],'negative_evidence')
    def test_block_and_case_estimand_are_different(self):
        p,r=fake_plan(3,1);p['cases'][0]['block']='one';p['cases'][1]['block']=p['cases'][2]['block']='two'
        r[0]['raw_score']=11
        p['metric']['aggregation']='case_mean';a=summarize(r,p,resamples=100)['mean_effect']
        p['metric']['aggregation']='block_mean';b=summarize(r,p,resamples=100)['mean_effect']
        self.assertEqual(a,4);self.assertEqual(b,5.5)
    def test_weighted_cases(self):
        p,r=fake_plan(2,1);p['cases'][0]['weight']=3;r[0]['raw_score']=3
        self.assertEqual(summarize(r,p,resamples=100)['mean_effect'],1.75)

class ProcessTests(unittest.TestCase):
    def test_nonzero(self):
        r=run([sys.executable,'-S','-c','raise SystemExit(7)'],cwd=ROOT,timeout=3);self.assertEqual(r['status'],'crash');self.assertEqual(r['returncode'],7)
    def test_missing_executable(self):self.assertEqual(run(['/not/a/command'],cwd=ROOT,timeout=1)['status'],'spawn_error')
    def test_timeout(self):self.assertEqual(run([sys.executable,'-S','-c','import time;time.sleep(3)'],cwd=ROOT,timeout=.15)['status'],'timeout')
    def test_output_limit(self):
        r=run([sys.executable,'-S','-c','print("x"*1000000)'],cwd=ROOT,timeout=3,max_output=1000)
        self.assertEqual(r['status'],'output_limit');self.assertLessEqual(len(r['stdout']),1000)
    def test_stderr_is_not_failure(self):
        r=run([sys.executable,'-S','-c','import sys; print("debug",file=sys.stderr)'],cwd=ROOT,timeout=3);self.assertEqual(r['status'],'ok')
    def test_env_isolation(self):
        os.environ['BOT_PARAMS']='must-not-leak'
        try:self.assertNotIn('BOT_PARAMS',clean_env())
        finally:os.environ.pop('BOT_PARAMS',None)
        with self.assertRaises(HarnessError):clean_env({'API_KEY':'secret'})
    def test_interactive_flush(self):
        b=LineBot([sys.executable,'-S','-u','-c','import sys\nfor x in sys.stdin: print("1",flush=True)'],cwd=ROOT)
        try:self.assertEqual(b.exchange('10 3\n',2),'1');self.assertEqual(b.exchange('9 3\n',2),'1')
        finally:b.close()
    def test_interactive_no_flush(self):
        code='import sys,time\nsys.stdin.readline()\nsys.stdout.write("1\\n")\ntime.sleep(3)'
        b=LineBot([sys.executable,'-S','-c',code],cwd=ROOT)
        try:
            with self.assertRaises(BotFault) as cm:b.exchange('10 3\n',.2)
            self.assertEqual(cm.exception.kind,'timeout')
        finally:b.close()

class BundleTests(unittest.TestCase):
    def test_comment_raw_literal(self):
        text='// #include "absent.h"\nconst char*s=R"tag(\n#include "absent.h"\n)tag";\n'
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'a.cpp').write_text(text);self.assertEqual(expand(p/'a.cpp',p),text)
    def test_conditional_pragma_once(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'x.hpp').write_text('#pragma once\nconstexpr int k=7;\n')
            (p/'a.cpp').write_text('#if 0\n#include "x.hpp"\n#endif\n#include "x.hpp"\nint main(){return k!=7;}\n')
            out=expand(p/'a.cpp',p);(p/'merged.cpp').write_text(out)
            r=run(['g++',str(p/'merged.cpp'),'-o',str(p/'program')],cwd=p,timeout=10)
            self.assertEqual(r['status'],'ok',r['stderr']);self.assertEqual(run([str(p/'program')],cwd=p,timeout=3)['status'],'ok')
    def test_cycle_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'a.hpp').write_text('#include "a.hpp"\n')
            with self.assertRaises(HarnessError):expand(p/'a.hpp',p)
    def test_macro_include_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'a.cpp').write_text('#include HEADER\n')
            with self.assertRaises(HarnessError):expand(p/'a.cpp',p)
    def test_digit_separator(self):self.assertIn("1'000",code_mask("int x=1'000;"))

if __name__=='__main__':unittest.main()
