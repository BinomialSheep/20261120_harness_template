#!/usr/bin/env python3
"""Initialize only a fresh local tracker, without hooks, skills or Dolt remotes."""
import argparse,json,os,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness.config import root as find_root
from harness import storage as S
from harness.util import HarnessError

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prefix',required=True);ap.add_argument('--allow-remote-discovery',action='store_true');a=ap.parse_args();root=find_root()
    S.require_integrator(root,'Beads initialization')
    if (root/'.beads/metadata.json').exists():raise HarnessError('Tracker already exists; verify/restore it instead of reinitializing')
    if S.git(root,'remote') and not a.allow_remote_discovery:raise HarnessError('Existing Git remote: bd init may discover it. Review before --allow-remote-discovery')
    from harness.util import safe_name
    safe_name(a.prefix)
    env={**os.environ,'BD_NON_INTERACTIVE':'1','BEADS_DIR':str(root/'.beads')}
    def bd(*args):
        p=subprocess.run(['bd',*args],cwd=root,env=env,capture_output=True,text=True,timeout=180)
        if p.returncode:raise HarnessError(p.stderr[:500])
        return p.stdout
    bd('metrics','off')
    version=bd('version')
    before={p:(root/p).read_bytes() for p in ('AGENTS.md','CLAUDE.md')}
    bd('--sandbox','init','--quiet','--skip-agents','--skip-hooks','--non-interactive','-p',a.prefix)
    bd('--sandbox','config','set','dolt.local-only','true');bd('--sandbox','config','set','backup.git-push','false')
    # No generic sync is invoked. Removing an automatically discovered remote is local configuration only.
    rem=bd('dolt','remote','list')
    if rem.strip():
        raise HarnessError('Tracker initialized, but inspect/remove discovered Dolt remotes locally before use; do NOT synchronize them')
    if any((root/p).read_bytes()!=v for p,v in before.items()):raise HarnessError('Agent entry file changed unexpectedly; inspect the diff')
    print(json.dumps({'version':version.strip(),'initialized':True,'next':'make beads-check; configure a local/off-disk backup; inspect bd prime against repository rules'}))
if __name__=='__main__':
    try:main()
    except (HarnessError,OSError,ValueError) as e:print(str(e),file=sys.stderr);raise SystemExit(2)
