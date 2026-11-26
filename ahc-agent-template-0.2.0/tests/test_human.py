import tempfile,unittest
from pathlib import Path
from tests.helpers import ROOT,make_project,git
from tools.check_docs import check
from harness import storage as S

class HumanDocs(unittest.TestCase):
    def test_links(self):self.assertTrue(check()['ok'],check()['errors'])
    def test_notes_are_not_scanned(self):
        with tempfile.TemporaryDirectory() as td:
            r=make_project(Path(td)/'p',full=True);(r/'human/local').mkdir();(r/'human/local/secret.md').write_text('DO NOT READ THIS [bad](../../missing-file)')
            before=S.read(r);self.assertTrue(check(r)['ok']);self.assertEqual(before,S.read(r))
    def test_notes_gitignored_not_guides(self):
        with tempfile.TemporaryDirectory() as td:
            r=make_project(Path(td)/'p',full=True)
            self.assertEqual(git(r,'check-ignore','human/local/private.md'),'human/local/private.md')
            self.assertEqual(git(r,'check-ignore','human/notes/thoughts.md'),'human/notes/thoughts.md')
            import subprocess
            p=subprocess.run(['git','check-ignore','human/guides/START_HERE.md'],cwd=r)
            self.assertNotEqual(p.returncode,0)
    def test_examples_are_advisory(self):
        t=(ROOT/'human/questions/parallel-budget.example.md').read_text();self.assertIn('実行許可ではない',t);self.assertIn('対象:',t)
    def test_new_project_has_no_delegated_research(self):
        # Also works after the user configures the real checkout. Never inspect its DB.
        with tempfile.TemporaryDirectory() as td:
            r=make_project(Path(td)/'p',full=True)
            self.assertIsNone(S.read(r)['payload']['mission'])
            self.assertEqual(S.read(r)['payload']['reservations'],{})
if __name__=='__main__':unittest.main()
