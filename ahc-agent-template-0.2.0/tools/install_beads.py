#!/usr/bin/env python3
"""Explicit, checksum-verified Linux release installation. Not called by tests or bootstrap."""
import argparse,hashlib,io,json,os,platform,sys,tarfile,urllib.request
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--download',action='store_true');p.add_argument('--version',default='1.3.1');p.add_argument('--out',default=str(Path.home()/'.local/bin/bd'));a=p.parse_args()
    if not a.download:raise SystemExit('Network/install require explicit --download; consult docs/BEADS.md')
    if platform.system()!='Linux':raise SystemExit('This installer supports Linux/WSL only')
    import re
    if not re.fullmatch(r'\d+\.\d+\.\d+',a.version):raise SystemExit('Explicit release version required')
    arch={'x86_64':'amd64','aarch64':'arm64'}.get(platform.machine())
    if not arch:raise SystemExit('Unsupported architecture')
    name=f'beads_{a.version}_linux_{arch}.tar.gz';base=f'https://github.com/gastownhall/beads/releases/download/v{a.version}/'
    def get(s):
        with urllib.request.urlopen(base+s,timeout=60) as r:return r.read(128*1024*1024)
    checks=get('checksums.txt').decode();expected=next((l.split()[0] for l in checks.splitlines() if l.split()[-1].lstrip('*')==name),None)
    if not expected:raise SystemExit('Release checksum missing; do not bypass verification')
    raw=get(name)
    if hashlib.sha256(raw).hexdigest()!=expected:raise SystemExit('Release checksum mismatch')
    dest=Path(a.out).expanduser()
    if dest.exists():raise SystemExit('Existing bd is not overwritten; verify the installed version first')
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as t:
        members=[m for m in t.getmembers() if Path(m.name).name=='bd' and m.isfile() and not m.issym() and not m.islnk()]
        if len(members)!=1:raise SystemExit('Unexpected release archive')
        data=t.extractfile(members[0]).read()
    dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open('xb') as f:f.write(data)
    dest.chmod(0o755)
    print(json.dumps({'installed':str(dest),'version':a.version,'archive_sha256':expected,'binary_sha256':hashlib.sha256(data).hexdigest(),'next':'bd metrics off; then tools/setup_beads.py. Do not commit the binary.'}))
if __name__=='__main__':main()
