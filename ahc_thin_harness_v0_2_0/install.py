#!/usr/bin/env python3
"""Deploy the harness into a contest project. Never overwrites an existing file; prints what it skipped.

    python3 path/to/ahc_thin_harness_v0_2_0/install.py [PROJECT_DIR] [--adapter batch|interactive] [--dry-run]

PROJECT_DIR defaults to the current directory. After this, follow SETUP.md from step 3.
"""
import argparse
import shutil
import sys
from pathlib import Path

if sys.version_info < (3, 10):
    sys.exit('Python 3.10 以上が必要')
SRC = Path(__file__).resolve().parent
# (source in the template, destination in the project)
FILES = [
    ('AGENTS.md', 'AGENTS.md'), ('README.md', 'HARNESS.md'), ('SETUP.md', 'docs/SETUP.md'), ('CHANGELOG.md', 'docs/HARNESS_CHANGELOG.md'),
    ('docs/OPERATIONS.md', 'docs/OPERATIONS.md'), ('docs/DESIGN.md', 'docs/DESIGN.md'), ('docs/VALIDATION.md', 'docs/HARNESS_VALIDATION.md'),
    ('config.example.json', 'config.example.json'), ('config.example.json', 'config.json'),
    ('templates/RESEARCH_LOG.md', 'docs/RESEARCH_LOG.md'), ('templates/ideas.example.json', 'templates/ideas.example.json'),
    ('templates/view.example.js', 'templates/view.example.js'), ('templates/claude_settings.json', '.claude/settings.json'),
    ('adapters/score_case.py', 'problem/score_case.py'), ('adapters/build_tools.sh', 'problem/build_tools.sh'),
    ('human/INBOX.md', 'human/INBOX.md'), ('human/private/README.md', 'human/private/README.md'),
]
DIRS = ['thin', 'worker', 'reference', 'tests', 'examples']
STATEMENT = '# 問題文\n\nここに問題文を置く（HTML なら Markdown に直す）。公式ツールは problem/tools/ に置く。\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project', nargs='?', default='.')
    ap.add_argument('--adapter', choices=['batch', 'interactive'], default='batch', help='対話型の問題なら interactive')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    dst = Path(a.project).resolve()
    if dst == SRC or SRC in dst.parents:
        sys.exit('雛形の中には配備しない（プロジェクトのフォルダを指定する）')
    done, skipped = [], []

    def put(src, rel, text=None):
        target = dst / rel
        if target.exists():
            skipped.append(rel)
            return
        done.append(rel)
        if a.dry_run:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if text is not None:
            target.write_text(text, encoding='utf-8')
        elif src.is_dir():
            shutil.copytree(src, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            shutil.copyfile(src, target)
    for d in DIRS:
        put(SRC / d, d)
    for s, d in FILES:
        put(SRC / s, d)
    put(None, 'problem/statement.md', STATEMENT)
    if not (dst / 'problem' / 'tools').exists():
        done.append('problem/tools/（空。公式ツールを置く）')
        if not a.dry_run:
            (dst / 'problem' / 'tools').mkdir(parents=True)
    ignore = (SRC / 'templates' / 'gitignore').read_text(encoding='utf-8').splitlines()
    gi = dst / '.gitignore'
    have = gi.read_text(encoding='utf-8').splitlines() if gi.is_file() else []
    add = [x for x in ignore if x and x not in have]
    if add:
        done.append(f'.gitignore に {len(add)} 行')
        if not a.dry_run:
            gi.write_text('\n'.join(have + ([''] if have else []) + add) + '\n', encoding='utf-8')
    if a.adapter == 'interactive' and not a.dry_run and 'config.json' in done:
        cfg = dst / 'config.json'
        cfg.write_text(cfg.read_text(encoding='utf-8').replace('"--mode", "batch"', '"--mode", "interactive"'), encoding='utf-8')
    print(('（試しに）' if a.dry_run else '') + f'{dst} に配備した: {len(done)} 件')
    for x in done:
        print('  +', x)
    for x in skipped:
        print('  = 既にあるので触らなかった:', x)
    print('次: SETUP.md の 3 から（問題文と公式ツールを置き、config.json を直し、python3 -m thin.cli doctor）。雛形のフォルダはもう消してよい。')


if __name__ == '__main__':
    main()
