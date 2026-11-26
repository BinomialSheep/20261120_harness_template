import json,subprocess,sys,tempfile,unittest,copy
from pathlib import Path
from unittest.mock import patch
from tests.helpers import make_project,mission,ROOT
from tests.fixtures.setup import prepare
from harness import storage as S,research as R,cases as C
from harness.artifacts import build,resolve
from harness.evaluate import run_evaluation
from harness.util import save,load,HarnessError

class Tools(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=make_project(Path(self.tmp.name)/'problem copy',full=True)
    def tearDown(self):self.tmp.cleanup()
    def cli(self,*args):return subprocess.run([sys.executable,'-m','harness',*args],cwd=self.root,capture_output=True,text=True,timeout=20)
    def test_bootstrap_without_regulations_or_network(self):
        self.assertIsNone(S.read(self.root)['payload']['mission'])
        self.assertFalse((self.root/'problem/supplied_rules/contest.md').exists())
        r=self.cli('doctor');self.assertEqual(r.returncode,0,r.stderr)
        self.assertFalse(json.loads(r.stdout)['atcoder_service_clients']);self.assertFalse(json.loads(r.stdout)['network_used'])
    def test_cli_case_import_verify(self):
        (self.root/'x.in').write_text('case input\n');save(self.root/'import.json',{'schema_version':1,'cases':[{'id':'a','path':'x.in'}]})
        r=self.cli('cases','import','--spec','import.json');self.assertEqual(r.returncode,0,r.stderr);sid=json.loads(r.stdout)['case_set_id']
        r=self.cli('cases','verify',sid);self.assertEqual(r.returncode,0,r.stderr)
    def test_no_contest_network_client(self):
        self.assertFalse((self.root/'integrations/codingame').exists())
        self.assertFalse((self.root/'integrations/atcoder').exists())
        for folder in ('harness','adapters','tools'):
            for f in (self.root/folder).rglob('*.py'):
                self.assertNotIn('https://atcoder.jp',f.read_text())
    def test_unknown_submission_operation_is_rejected(self):
        with self.assertRaises(HarnessError):mission(self.root,{'submissions':1},{'submit':True})
    def test_audit_without_exposure_reference_stops(self):
        mission(self.root);path,s=prepare(self.root,'max',1);s['purpose']='audit';save(self.root/path,s)
        a=build(self.root,'tests/fixtures/selector.cpp','candidate');R.reserve(self.root,'w',['evaluation'],{'solver_runs':2})
        with self.assertRaises(HarnessError):run_evaluation(self.root,path,a['artifact_id'],a['artifact_id'],'audit',1,work='w')
        self.assertEqual(S.read(self.root)['payload']['reservations']['w']['used'],{})
    def test_timing_sensitive_requires_serial(self):
        mission(self.root);path,s=prepare(self.root,'max',1);s['timing_sensitive']=True;save(self.root/path,s)
        with self.assertRaises(HarnessError):run_evaluation(self.root,path,'x','y','timing',2,work='w')
    def test_prepare_submission_only_generates_local_files(self):
        from tools.prepare_submission import prepare as prepare_submit
        from tools.test import fingerprint
        mission(self.root);path,s=prepare(self.root,'max',2);s['purpose']='smoke';s['variants']={};save(self.root/path,s)
        a=build(self.root,'tests/fixtures/selector.cpp','deploy');R.reserve(self.root,'w',['evaluation'],{'solver_runs':4})
        o=run_evaluation(self.root,path,'deploy','deploy','smoke',1,work='w')
        # Unit-level preflight uses a synthetic regression receipt. Full package tests are run separately.
        save(self.root/'reports/local-tests.json',{'ok':True,'skipped':0,'source_fingerprint':fingerprint(self.root)})
        r=prepare_submit(self.root,a['artifact_id'],o['run_id'],'dist/submission')
        self.assertTrue((Path(r['directory'])/'main.cpp').exists());self.assertEqual(r['receipt']['external_operations'],0)
        self.assertNotIn('submitted',S.read(self.root)['payload']['refs'])
        with self.assertRaises(HarnessError):prepare_submit(self.root,a['artifact_id'],o['run_id'],'dist/submission')
    def test_runtime_parameters_cannot_be_misrepresented_as_submission(self):
        from tools.prepare_submission import prepare as prepare_submit
        mission(self.root);path,s=prepare(self.root,'max',1);s['purpose']='smoke';save(self.root/path,s)
        a=build(self.root,'tests/fixtures/selector.cpp','param');R.reserve(self.root,'w',['evaluation'],{'solver_runs':2})
        o=run_evaluation(self.root,path,'param','param','smoke',1,work='w')
        with self.assertRaises(HarnessError):prepare_submit(self.root,a['artifact_id'],o['run_id'],'dist/submission')
    def test_export_context_no_api_or_private_notes(self):
        (self.root/'human/local').mkdir();(self.root/'human/local/private.md').write_text('PRIVATE-UNRELATED-CONTENT')
        out=self.root/'collaboration/packet'
        p=subprocess.run([sys.executable,'tools/export_context.py','--out',str(out)],cwd=self.root,capture_output=True,text=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stderr);self.assertNotIn('PRIVATE-UNRELATED-CONTENT',(out/'context.md').read_text())
    def test_optional_github_preview_is_explicit_and_nonbinding(self):
        from integrations.beads import github_issues as G
        project=load(self.root/'project.json');project['github_repository']='owner/repo';save(self.root/'project.json',project)
        row={'id':'ahc-x','title':'independent branch','description':'Evidence in report','labels':['share:github']}
        with patch.object(G,'issue',return_value=row):
            p=G.preview(self.root,'ahc-x');self.assertEqual(p['action'],'create')
        row['labels'].append('proposal')
        with patch.object(G,'issue',return_value=row),self.assertRaises(HarnessError):G.preview(self.root,'ahc-x')
        self.assertIsNone(S.read(self.root)['payload']['mission'])
    def test_case_generation_partial_failure_retained(self):
        mission(self.root,{'generated_cases':3},{'case_generation':True});(self.root/'g.py').write_text('import sys\ns=int(sys.argv[1])\nif s==1:raise SystemExit(2)\nprint(s)\n')
        R.reserve(self.root,'g',['case_generation'],{'generated_cases':3})
        with self.assertRaises(HarnessError):C.generate(self.root,{'schema_version':1,'files':['g.py'],'command':['{python}','-S','{oracle}/g.py','{seed}'],'seeds':[0,1,2]},'g')
        states=list(S.evidence(self.root,'imports').glob('generation-*/state.json'));self.assertEqual(len(states),1)
        s=load(states[0]);self.assertEqual(s['status'],'failed');self.assertEqual(s['completed'],1)
        self.assertEqual(S.read(self.root)['payload']['reservations']['g']['used']['generated_cases'],3)
    def test_output_contract_cannot_replace_interactive_log(self):
        from harness.judge import score_output
        mission(self.root,{'judge_runs':1},{'rescore':True});path,s=prepare(self.root,'interactive',1)
        p=self.root/'x.out';p.write_text('! 30\n');R.reserve(self.root,'j',['rescore'],{'judge_runs':1})
        with self.assertRaises(HarnessError):score_output(self.root,s['case_set_id'],'c0000',p,s['contract'],'j')
if __name__=='__main__':unittest.main()
