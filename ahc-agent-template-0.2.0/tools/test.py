#!/usr/bin/env python3
"""Offline regression suite; no account operations or package downloads."""
import argparse,json,sys,unittest,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from harness.util import save,now,sha,digest

def fingerprint(root=ROOT):
    files={}
    for folder in ('harness','adapters','integrations','tools','tests','problem'):
        for p in (root/folder).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py','.java','.cpp','.json','.toml'):
                files[str(p.relative_to(root))]=sha(p)
    files['harness.toml']=sha(root/'harness.toml')
    return digest(files)

def main():
    for k in list(os.environ):
        if k.startswith('GIT_'): os.environ.pop(k)
    p=argparse.ArgumentParser();p.add_argument('--strict',action='store_true');p.add_argument('--record',action='store_true');a=p.parse_args()
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT/'tests'),top_level_dir=str(ROOT)))
    ok=result.wasSuccessful() and (not a.strict or not result.skipped)
    info={'at':now(),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'ok':ok,
          'source_fingerprint':fingerprint(),'network':'none (API calls use fakes)','live_beads':'separate acceptance','official_problem':'separate per-game integration'}
    if a.record:save(ROOT/'reports/local-tests.json',info)
    print(json.dumps(info,ensure_ascii=False,indent=2));return 0 if ok else 1
if __name__=='__main__':raise SystemExit(main())
