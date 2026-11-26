"""Installed-template CLI tests use independent Git copies, never the user's repository state."""
import json,subprocess,sys,tempfile,unittest
from pathlib import Path
from tests.helpers import make_project,mission
from harness import research as R,storage as S

class CopyAndCLI(unittest.TestCase):
    def cli(self,r,*args):
        return subprocess.run([sys.executable,'-m','harness',*args],cwd=r,capture_output=True,text=True,timeout=30)
    def test_different_games_get_different_identity(self):
        with tempfile.TemporaryDirectory() as t:
            a=make_project(Path(t)/'copy A',full=True);b=make_project(Path(t)/'copy B',full=True)
            self.assertNotEqual(S.read(a)['store_id'],S.read(b)['store_id'])
            self.assertNotEqual(S.read(a)['payload']['project_id'],S.read(b)['payload']['project_id'])
    def test_cli_operates_from_copied_folder_with_spaces(self):
        with tempfile.TemporaryDirectory() as t:
            r=make_project(Path(t)/'game with spaces',full=True)
            q=self.cli(r,'doctor');self.assertEqual(q.returncode,0,q.stderr)
            self.assertTrue(json.loads(q.stdout)['configured'])
            q=self.cli(r,'render');self.assertEqual(q.returncode,0,q.stderr)
            self.assertTrue((r/'docs/STATUS.md').exists())
    def test_cli_run_after_double_dash_and_usage(self):
        with tempfile.TemporaryDirectory() as t:
            r=make_project(Path(t)/'p',full=True);mission(r)
            R.reserve(r,'task-cli',['training'],{'training_runs':1})
            q=self.cli(r,'work','run','task-cli','--op','training','--timeout','10','--',sys.executable,'-c','print("trial-result")')
            self.assertEqual(q.returncode,0,q.stderr)
            self.assertEqual(json.loads(q.stdout)['status'],'ok')
            self.assertIn('trial-result',json.loads(q.stdout)['stdout'])
            self.assertEqual(S.read(r)['payload']['reservations']['task-cli']['used']['training_runs'],1)
    def test_collection_requires_explicit_game_count(self):
        with tempfile.TemporaryDirectory() as t:
            r=make_project(Path(t)/'p',full=True)
            mission(r,{'generated_cases':20},{'case_generation':True})
            R.reserve(r,'collect',['case_generation'],{'generated_cases':20})
            q=self.cli(r,'work','run','collect','--op','case_generation','--',sys.executable,'-c','print("must not execute")')
            self.assertEqual(q.returncode,2);self.assertNotIn('must not execute',q.stdout)
            q=self.cli(r,'work','run','collect','--op','case_generation','--amount','generated_cases=20','--',sys.executable,'-c','print("collection fixture")')
            self.assertEqual(q.returncode,0,q.stderr)
            self.assertEqual(S.read(r)['payload']['reservations']['collect']['used']['generated_cases'],20)
    def test_foreign_git_environment_does_not_redirect_bootstrap(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as t:
            a=make_project(Path(t)/'a',full=True)
            old=S.read(a)
            with patch.dict('os.environ',{'GIT_DIR':str(a/'.git'),'GIT_WORK_TREE':str(a),'GIT_INDEX_FILE':str(a/'.git/foreign-index')}):
                b=make_project(Path(t)/'b',full=True)
                self.assertNotEqual(S.read(a)['store_id'],S.read(b)['store_id'])
            self.assertEqual(S.read(a),old)
            self.assertFalse((a/'.git/foreign-index').exists())

    def test_changed_upstream_target_stays_non_executable(self):
        with tempfile.TemporaryDirectory() as t:
            r=make_project(Path(t)/'p',full=True)
            from tools.bootstrap import configure
            from harness.util import HarnessError
            with self.assertRaises(HarnessError):configure(r,'unrelated-game','fixture','',False)
            self.assertIsNone(S.read(r)['payload']['mission'])

if __name__=='__main__':unittest.main()
