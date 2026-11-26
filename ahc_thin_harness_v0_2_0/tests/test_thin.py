"""Tests without any LLM: python3 -m unittest discover -s tests -v (about 30 seconds)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from thin import cli, core, engines, scoring, usage  # noqa: E402
from thin.util import ThinError, read_json, write_json  # noqa: E402


def toy_project(tmp, **config):
    """A deployed toy project with the demo engine; returns its root."""
    root = Path(tmp) / 'proj'
    for d in ('thin', 'worker'):
        shutil.copytree(HARNESS / d, root / d, ignore=shutil.ignore_patterns('__pycache__'))
    toy = HARNESS / 'examples' / 'toy'
    (root / 'problem').mkdir()
    shutil.copyfile(toy / 'statement.md', root / 'problem' / 'statement.md')
    shutil.copyfile(toy / 'score_case.py', root / 'problem' / 'score_case.py')
    cfg = json.loads((toy / 'config.json').read_text())
    for k, v in config.items():
        cfg[k] = {**cfg.get(k, {}), **v}
    write_json(root / 'config.json', cfg)
    (root / 'demo_assets').mkdir()
    shutil.copyfile(toy / 'fake_agent.py', root / 'demo_assets' / 'fake_agent.py')
    for f in toy.glob('variant_*.cpp'):
        shutil.copyfile(f, root / 'demo_assets' / f.name)
    (root / 'docs').mkdir()
    shutil.copyfile(HARNESS / 'templates' / 'RESEARCH_LOG.md', root / 'docs' / 'RESEARCH_LOG.md')
    core.init_best(root, toy / 'baseline.cpp')
    return root


def ideas_file(root, names, **extra):
    p = root / 'ideas.json'
    write_json(p, {'common': '模擬', 'ideas': [{'title': n, 'kind': 'continue', 'idea': f'demo_assets の {n}.cpp を写す', **extra} for n in names]})
    return p


class TestIdeas(unittest.TestCase):
    def test_rules(self):
        common, ideas = core.parse_ideas('B', idea='best を深く改善する長い課題文', title='深く')
        self.assertEqual((ideas[0]['id'], ideas[0]['kind'], ideas[0]['parents']), ('B01', 'deep', ['best']))
        with self.assertRaises(ThinError):
            core.parse_ideas('A', idea='x' * 20)  # A needs JSON? no: one idea is fine, but kind continue needs a parent
            raise ThinError('ok')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'i.json'
            write_json(p, {'ideas': [{'kind': 'combine', 'idea': '二つを組み合わせる課題', 'parents': ['best']}]})
            with self.assertRaises(ThinError):
                core.parse_ideas('A', p)
            write_json(p, {'ideas': [{'kind': 'new', 'idea': '新しい作り方の課題文', 'title': 'x' * 41}]})
            with self.assertRaises(ThinError):
                core.parse_ideas('A', p)
            write_json(p, {'ideas': [{'kind': 'new', 'idea': f'新しい作り方の課題文 {i}'} for i in range(11)]})
            with self.assertRaises(ThinError):
                core.parse_ideas('A', p)

    def test_render_and_outline(self):
        t = 'a {x}\n@if A|B\nab\n@end\n@if !tuning\nno-tune\n@end\n@if C\nc\n@end\n'
        self.assertEqual(core.render(t, {'x': 1}, {'A': True}), 'a 1\nab\nno-tune\n')
        src = 'struct S {\n};\nint solve(int n) {\n  if (n) {\n  }\n}\nint main()\n{\n}\nint g(int);\n'
        out = core.outline(src)
        self.assertIn('struct S', out)
        self.assertIn('solve', out)
        self.assertIn('main', out)
        self.assertNotIn('if (n)', out)
        self.assertNotIn('g(int);', out)


class TestLoop(unittest.TestCase):
    def test_round_a(self):
        with tempfile.TemporaryDirectory() as d:
            root = toy_project(d)
            prep = core.prepare(root, 'A', ideas_path=ideas_file(root, ['variant_a', 'variant_b', 'variant_c', 'variant_d']))
            ws = Path(prep['workspaces'])
            self.assertFalse(ws.is_relative_to(root))
            self.assertTrue((ws / 'A01' / 'main.cpp').is_file())  # parent_1 copied as the starting point
            r = cli.go(root, prep['round_id'])
            ev = r['evaluation']
            by = {c['id']: c for c in ev['candidates']}
            self.assertEqual(ev['recommended'], 'A01')
            self.assertFalse(by['A04']['valid'])
            self.assertTrue(any(c['status'] == 'dropped' for c in ev['candidates']))
            self.assertLess(ev['cost']['cases'], ev['cost']['full_dev_cases'])
            scoring.promote(root, prep['round_id'], 'A01')
            self.assertTrue((root / 'best' / 'notes.md').is_file())
            out = core.close(root, prep['round_id'], note='テスト')
            self.assertEqual(out['research_log'], 'ok')
            self.assertIn('| R0001 | A |', (root / 'docs' / 'RESEARCH_LOG.md').read_text())
            self.assertTrue((root / 'report' / 'index.html').is_file(), out)
            # next round: B with two parents and a reference file
            (root / 'note.txt').write_text('参考')
            prep2 = core.prepare(root, 'B', idea='demo_assets の variant_b.cpp を写す', title='B の試し',
                                 parents=['best', 'R0001/A02'], refs=['note.txt'])
            w2 = Path(prep2['workspaces']) / 'B01'
            self.assertTrue((w2 / 'parent_2.diff').is_file() and (w2 / 'ref' / 'note.txt').is_file())
            r2 = cli.go(root, prep2['round_id'])
            self.assertEqual(r2['evaluation']['candidates'][0]['n'], 60)  # B: no stages, no confirmation
            self.assertIsNone(r2['evaluation']['confirm'])

    def test_confirmation_decides(self):
        with tempfile.TemporaryDirectory() as d:
            root = toy_project(d, evaluation={'skip_confirm_z': 1e9})
            prep = core.prepare(root, 'A', ideas_path=ideas_file(root, ['variant_a', 'variant_c']))
            ev = cli.go(root, prep['round_id'])['evaluation']
            self.assertEqual(ev['confirm']['id'], 'A01')
            self.assertEqual(ev['confirm']['n'], 60)
            lead = next(c for c in ev['candidates'] if c['id'] == 'A01')
            self.assertEqual(lead['decision'], 'confirm')
            # resuming changes nothing
            again = scoring.evaluate(root, prep['round_id'])
            self.assertEqual(again['cost']['cases'], 0)
            # restart moves the old evaluation away and scores again
            fresh = scoring.evaluate(root, prep['round_id'], restart=True)
            self.assertGreater(fresh['cost']['cases'], 0)
            self.assertTrue(list((root / 'runs' / prep['round_id']).glob('evaluation.old-*')))

    def test_interrupted_and_leftovers(self):
        with tempfile.TemporaryDirectory() as d:
            root = toy_project(d)
            agent = root / 'demo_assets' / 'fake_agent.py'
            agent.write_text(textwrap.dedent('''\
                import shutil, subprocess, sys
                from pathlib import Path
                w = Path.cwd()
                shutil.copyfile(w / 'variant_a.cpp', w / 'main.cpp')
                subprocess.Popen(['setsid', 'sleep', '300'], cwd=w, start_new_session=True)
                if 'variant_b' in (w / 'task.md').read_text():
                    print("You've hit your 5-hour limit · resets 4am")
                    sys.exit(1)
                '''))
            prep = core.prepare(root, 'A', ideas_path=ideas_file(root, ['variant_a', 'variant_b']))
            res = engines.run_workers(root, prep['round_id'])
            st = {w['id']: w for w in res['workers']}
            self.assertEqual(st['A02']['status'], 'interrupted')
            self.assertEqual(res['waiting'], ['A02'])
            job = read_json(root / 'runs' / prep['round_id'] / 'jobs' / 'A01' / 'job.json')
            self.assertGreaterEqual(job['leftover_killed'], 1)
            time.sleep(0.3)
            from thin.process import processes_in
            self.assertEqual(processes_in(Path(prep['workspaces'])), [])
            with self.assertRaises(ThinError):
                scoring.evaluate(root, prep['round_id'])  # A02 still waiting
            core.accept(root, prep['round_id'], 'A02')
            core.mark_workers_done(root, prep['round_id'])
            ev = scoring.evaluate(root, prep['round_id'])
            self.assertEqual(len(ev['candidates']), 2)

    def test_measure_round_and_background(self):
        with tempfile.TemporaryDirectory() as d:
            root = toy_project(d, workers={'tuning': {'seed_start': 900000, 'seed_count': 100, 'parallelism': 2}})
            agent = root / 'demo_assets' / 'fake_agent.py'
            agent.write_text(textwrap.dedent('''\
                import json, subprocess, sys
                from pathlib import Path
                w = Path.cwd()
                r = subprocess.run([sys.executable, 'batchcheck.py', '--seeds', '900000-900019', '--baseline', 'variant_a.cpp'],
                                   capture_output=True, text=True)
                (w / 'out' / 'batch.txt').write_text(r.stdout + r.stderr)
                (w / 'report.md').write_text('測った')
                '''))
            prep = core.prepare(root, 'C', idea='variant_a と比べて何が違うかを測る', title='測定')
            ws = Path(prep['workspaces']) / 'C01'
            self.assertTrue((ws / 'batchcheck.py').is_file() and (ws / 'slowclock.c').is_file() and (ws / 'out').is_dir())
            shutil.copyfile(ws / 'variant_a.cpp', ws / 'main.cpp')
            r = cli.go(root, prep['round_id'])
            self.assertNotIn('evaluation', r)
            rd = root / 'runs' / prep['round_id']
            self.assertEqual(read_json(rd / 'jobs' / 'C01' / 'job.json')['status'], 'done')
            self.assertTrue((rd / 'jobs' / 'C01' / 'report.md').is_file() and (rd / 'jobs' / 'C01' / 'out' / 'batch.txt').is_file())
            core.close(root, prep['round_id'], report=False)
            # batchcheck itself: paired comparison against a baseline on allowed tuning seeds
            out = subprocess.run([sys.executable, 'batchcheck.py', '--seeds', '900000-900029', '--baseline', 'variant_c.cpp'],
                                 cwd=ws, capture_output=True, text=True)
            res = json.loads(out.stdout)
            self.assertGreater(res['paired']['diff'], 0)
            self.assertIn('bands', res)
            bad = subprocess.run([sys.executable, 'batchcheck.py', '--seeds', '1000-1001'], cwd=ws, capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)  # evaluation seeds are not allowed
            shutil.copyfile(HARNESS / 'examples' / 'toy' / 'fake_agent.py', agent)  # back to the copying worker
            prep2 = core.prepare(root, 'B', idea='demo_assets の variant_a.cpp を写す', title='裏で')
            bg = cli.go(root, prep2['round_id'], background=True)
            for _ in range(600):
                if read_json(root / 'runs' / prep2['round_id'] / 'state.json')['phase'] == 'evaluated':
                    break
                time.sleep(0.2)
            self.assertEqual(read_json(root / 'runs' / prep2['round_id'] / 'state.json')['phase'], 'evaluated', bg)

    def test_judge_failure_stops(self):
        with tempfile.TemporaryDirectory() as d:
            root = toy_project(d)
            prep = core.prepare(root, 'A', ideas_path=ideas_file(root, ['variant_a']))
            engines.run_workers(root, prep['round_id'])
            judge = root / 'runs' / prep['round_id'] / 'problem' / 'score_case.py'
            judge.write_text(judge.read_text().replace("if a.seed >= 0", "if a.seed >= 0") + '\nimport sys\nif a.seed == 1005: sys.exit(3)\n')
            plan = read_json(root / 'runs' / prep['round_id'] / 'plan.json')
            plan['problem_sha256'] = core.tree_hash(root / 'runs' / prep['round_id'] / 'problem')
            write_json(root / 'runs' / prep['round_id'] / 'plan.json', plan)
            with self.assertRaises(ThinError) as e:
                scoring.evaluate(root, prep['round_id'])
            self.assertIn('環境', str(e.exception))
            job = read_json(root / 'runs' / prep['round_id'] / 'jobs' / 'A01' / 'job.json')
            self.assertEqual(job['status'], 'submitted')  # not marked invalid


class TestPieces(unittest.TestCase):
    def test_classify(self):
        with tempfile.TemporaryDirectory() as d:
            j = Path(d)
            (j / 'agent.stdout').write_text('\n'.join(json.dumps({'type': 'error', 'message': f'Reconnecting... {i}/5 (stream disconnected)'})
                                                       for i in range(1, 6)) + '\n')
            (j / 'agent.stderr').write_text('')
            self.assertEqual(engines.classify(j, {'status': 'timeout'})[0], 'network')
            (j / 'agent.stdout').write_text(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message',
                                                                                          'text': 'usage limit の話をしただけ'}}) + '\n')
            self.assertEqual(engines.classify(j, {'status': 'ok'})[0], 'ok')
            (j / 'agent.stdout').write_text(json.dumps({'type': 'turn.failed', 'error': {'message': "You've hit your usage limit."}}) + '\n')
            self.assertEqual(engines.classify(j, {'status': 'error'})[0], 'limit')

    def test_waits(self):
        self.assertTrue(usage.is_wait({'name': 'wait', 'arguments': '{"cell_id":"1","yield_time_ms":1000}'}))
        self.assertTrue(usage.is_wait({'name': 'write_stdin', 'arguments': '{"session_id":1,"chars":""}'}))
        self.assertFalse(usage.is_wait({'name': 'write_stdin', 'arguments': '{"session_id":1,"chars":"y"}'}))
        self.assertFalse(usage.is_wait({'name': 'exec', 'arguments': 'ls'}))

    def test_claude_stream_totals(self):
        with tempfile.TemporaryDirectory() as d:
            j = Path(d)
            msg = lambda i, out: {'type': 'assistant', 'message': {'id': i, 'usage': {'input_tokens': 1, 'cache_read_input_tokens': 100,
                                                                                    'output_tokens': out}}}
            res = {'type': 'result', 'usage': {'input_tokens': 2, 'cache_read_input_tokens': 200, 'output_tokens': 900,
                                               'output_tokens_details': {'thinking_tokens': 300}}}
            (j / 'agent.stdout').write_text('\n'.join(json.dumps(x) for x in (msg('a', 3), msg('a', 3), msg('b', 5), res)) + '\n')
            u = usage.worker_summary(j)
            self.assertEqual((u['calls'], u['out'], u['think'], u['cr']), (2, 900, 300, 200))

    def test_adapter(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            shutil.copyfile(HARNESS / 'adapters' / 'score_case.py', d / 'score_case.py')
            (d / 'bin').mkdir()
            (d / 'bin' / 'gen').write_text('#!/bin/sh\nmkdir -p in\nread s < seeds.txt\necho "5 $s 7" > in/0000.txt\n')
            (d / 'bin' / 'vis').write_text('#!/bin/sh\nread a b c < "$1"\nn=$(cat "$2")\nif [ "$n" = bad ]; then echo "Invalid output"; echo "Score = 0"; '
                                           'else echo "Score = $((a + n))"; fi\n')
            for f in ('gen', 'vis'):
                os.chmod(d / 'bin' / f, 0o755)
            sol = d / 'sol.sh'

            def judge(body, *extra):
                sol.write_text('#!/bin/sh\n' + body)
                os.chmod(sol, 0o755)
                subprocess.run([sys.executable, str(d / 'score_case.py'), '--binary', str(sol), '--seed', '3', '--result', str(d / 'r.json'),
                                '--features', 'N,M,-', '--time-limit', '0.5', *extra], check=True)
                return json.loads((d / 'r.json').read_text())
            r = judge('echo 10\n')
            self.assertEqual((r['valid'], r['score'], r['features']), (True, 15, {'N': 5, 'M': 3}))
            self.assertLess(r['elapsed_sec'], 0.4)
            self.assertTrue(judge('echo bad\n')['reason'].startswith('WA'))
            self.assertTrue(judge('sleep 2\necho 1\n')['reason'].startswith('TLE'))
            self.assertTrue(judge('exit 3\n')['reason'].startswith('RE'))
            self.assertTrue(judge('sleep 0.7\necho 1\n', '--time-scale', '2')['valid'])
            os.remove(d / 'bin' / 'vis')
            p = subprocess.run([sys.executable, str(d / 'score_case.py'), '--binary', str(sol), '--seed', '3', '--result', str(d / 'x.json')])
            self.assertEqual(p.returncode, 2)
            self.assertFalse((d / 'x.json').exists())

    def test_install(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / 'p'
            proj.mkdir()
            (proj / 'AGENTS.md').write_text('既存')
            subprocess.run([sys.executable, str(HARNESS / 'install.py'), str(proj)], check=True, capture_output=True)
            self.assertEqual((proj / 'AGENTS.md').read_text(), '既存')
            for f in ('config.json', 'HARNESS.md', 'thin/cli.py', 'problem/score_case.py', 'docs/RESEARCH_LOG.md', '.gitignore',
                      '.claude/settings.json', 'human/INBOX.md', 'reference/cpp_notes.md'):
                self.assertTrue((proj / f).exists(), f)
            self.assertNotIn('config.json', (proj / '.gitignore').read_text().splitlines())


if __name__ == '__main__':
    unittest.main()
