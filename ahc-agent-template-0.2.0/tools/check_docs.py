#!/usr/bin/env python3
"""Check curated shared documentation only. Never read private notes or execute text in them."""
import json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.util import load

def check(root=ROOT):
    root=Path(root);m=load(root/'human/manifest.json');errors=[];seen=[]
    for name in m['files']:
        if name.startswith('human/local/') or (name.startswith('human/notes/') and Path(name).name!='README.md' and not name.endswith('.example.md')):
            errors.append('Private file listed in shared manifest: '+name);continue
        p=root/name
        if not p.is_file():errors.append('Missing '+name);continue
        seen.append(name);text=p.read_text(encoding='utf-8')
        for target in re.findall(r'\]\(([^)]+)\)',text):
            if '://' in target or target.startswith('#'):continue
            dest=(p.parent/target.split('#',1)[0]).resolve()
            if not dest.is_relative_to(root.resolve()) or not dest.exists():errors.append(name+': broken link '+target)
    return {'ok':not errors,'checked':len(seen),'errors':errors,'private_notes_read':False,'network_used':False}
if __name__=='__main__':
    d=check();print(json.dumps(d,ensure_ascii=False,indent=2));raise SystemExit(0 if d['ok'] else 1)
