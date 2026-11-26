"""Seal data/model/output directories and cache derived data by content, never basename/mtime."""
from __future__ import annotations
import json, os, shutil, tempfile, uuid
from pathlib import Path
from . import storage as S
from .util import HarnessError, contained, digest, load, lock, now, save, sha, safe_name

def files_under(path):
    path=Path(path).resolve()
    if not path.is_dir(): raise HarnessError('Seal a directory, with files placed inside it')
    out={}
    for f in sorted(path.rglob('*')):
        if f.is_symlink(): raise HarnessError('Symlinks are not allowed in sealed evidence')
        if f.is_file(): out[str(f.relative_to(path))]=sha(f)
    if not out: raise HarnessError('Empty evidence directory')
    return out

def seal(root,source,kind,producer=None,key_fields=()):
    if kind not in ('datasets','models','sealed'): raise HarnessError('kind must be datasets/models/sealed')
    src=contained(root,source); files=files_under(src); rows=0
    if key_fields:
        keys=set()
        for rel in files:
            if not rel.endswith('.jsonl'): continue
            with (src/rel).open() as f:
                for line in f:
                    if not line.strip(): continue
                    d=json.loads(line)
                    try: k=tuple(d[x] for x in key_fields)
                    except KeyError as e: raise HarnessError('Missing dataset key '+str(e)) from e
                    if k in keys: raise HarnessError('Duplicate dataset key: '+repr(k))
                    keys.add(k); rows+=1
    identity={'schema_version':1,'kind':kind,'files':files,'producer':producer or {'source':'unknown'},'key_fields':list(key_fields)}
    eid=digest(identity); dest=S.evidence(root,kind)/eid
    with lock(S.base(root)/'locks'/('seal-'+eid+'.lock')):
        if not dest.exists():
            tmp=Path(tempfile.mkdtemp(prefix='.seal-',dir=dest.parent))
            try:
                shutil.copytree(src,tmp/'payload')
                if files_under(tmp/'payload')!=files: raise HarnessError('Data changed while sealing')
                save(tmp/'manifest.json',{'id':eid,'identity':identity,'created_at':now(),'rows_checked':rows})
                os.replace(tmp,dest)
            finally:
                if tmp.exists(): shutil.rmtree(tmp)
    with S.transaction(root) as p:
        p['outputs'][eid]={'kind':kind,'source_workspace':str(Path(root).resolve()),'source_path':str(src.relative_to(root)),'files':files}
        S.event(p,'evidence_sealed',id=eid,kind_=kind)
    return {'id':eid,'kind':kind,'path':str(dest),'quality_verified':False,'rows_checked':rows}

def verify(root,kind,eid):
    safe_name(eid); dest=S.evidence(root,kind)/eid; m=load(dest/'manifest.json')
    if m['id']!=eid or digest(m['identity'])!=eid or files_under(dest/'payload')!=m['identity']['files']:
        raise HarnessError('Sealed evidence has changed')
    return m

def cache_key(inputs,transform_file,options):
    return digest({'inputs':[{'sha256':sha(Path(p))} for p in inputs], 'transform_sha256':sha(transform_file),'options':options})

def cache_store(root,key,payload):
    safe_name(key); path=S.base(root)/'cache'/key
    save(path,{'key':key,'payload':payload,'sha256':digest(payload)})

def cache_load(root,key):
    safe_name(key); path=S.base(root)/'cache'/key
    if not path.exists(): return None
    try:
        d=load(path)
        return d['payload'] if d.get('key')==key and d.get('sha256')==digest(d.get('payload')) else None
    except (ValueError,KeyError,OSError): return None
