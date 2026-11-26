"""Frozen C++ source and executables in the clone's shared content-addressed evidence store."""
from __future__ import annotations
import os, platform, re, shlex, shutil, tempfile
from pathlib import Path
from . import IDENTITY_VERSION
from . import storage as S
from .config import read
from .process import run
from .util import HarnessError, contained, digest, load, now, safe_name, save, sha, lock

PROTECTED={'submitted','best_local','submit_candidate','baseline'}
def refkey(root,name): return 'dev:'+digest(str(Path(root).resolve()))[:12]+':'+name

def resolve(root,name):
    safe_name(name)
    refs=S.read(root)['payload']['refs']
    aid=refs.get(refkey(root,name),refs.get(name,{})).get('artifact_id',name)
    if not re.fullmatch('[0-9a-f]{64}',aid): raise HarnessError('Unknown artifact/ref: '+name)
    d=S.evidence(root,'objects')/aid; m=load(d/'manifest.json')
    if m.get('artifact_id')!=aid or digest(m['identity'])!=aid: raise HarnessError('Artifact manifest mismatch')
    if sha(d/'program')!=m['binary_sha256']: raise HarnessError('Frozen executable changed')
    for rel,h in m['identity']['files'].items():
        if sha(contained(d/'snapshot',rel))!=h: raise HarnessError('Frozen input changed: '+rel)
    return {**m,'directory':str(d),'command':[str(d/'program')]}

def build(root,source,name,profile='release',assets=()):
    root=Path(root).resolve(); safe_name(name)
    if name in PROTECTED: raise HarnessError('Build a candidate; shared refs move only by explicit adoption/submission')
    conf=read(root)
    if profile not in conf['build']['profiles']: raise HarnessError('Unknown compiler profile')
    src=contained(root,source); rel=str(src.relative_to(root)); flags=conf['build']['profiles'][profile]['flags']
    if any(x.startswith(('-I','-iquote','-isystem','-include','-o','@','-fplugin')) for x in flags): raise HarnessError('Profile has unsupported include/output/plugin flags')
    cc=shutil.which(conf['build']['compiler'])
    if not cc: raise HarnessError('C++ compiler missing')
    ver=run([cc,'--version'],cwd=root,timeout=10)
    dep=run([cc,*flags,'-I',str(root),'-MM','-MT','target',str(src)],cwd=root,timeout=30)
    if dep['status']!='ok': raise HarnessError('Dependency discovery failed: '+dep['stderr'])
    text=dep['stdout'].replace('\\\n',' ')
    if ':' not in text: raise HarnessError('Invalid compiler dependency output')
    paths=set(shlex.split(text.split(':',1)[1])); paths.add(str(src)); paths.update(str(contained(root,a)) for a in assets)
    files={str(contained(root,x).relative_to(root)):sha(contained(root,x)) for x in paths}
    identity={'identity_version':IDENTITY_VERSION,'source':rel,'files':files,'profile':profile,'flags':flags,
              'compiler':ver['stdout'].splitlines()[0],'compiler_sha256':sha(cc),'machine':platform.machine()}
    aid=digest(identity); objects=S.evidence(root,'objects'); dest=objects/aid
    with lock(S.base(root)/'locks'/('build-'+aid+'.lock')):
        if not dest.exists():
            temp=Path(tempfile.mkdtemp(prefix='.build-',dir=objects))
            try:
                for path,h in files.items():
                    out=temp/'snapshot'/path; out.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(root/path,out)
                    if sha(out)!=h: raise HarnessError('Input changed while freezing')
                r=run([cc,*flags,'-I',str(temp/'snapshot'),str(temp/'snapshot'/rel),'-o',str(temp/'program')],cwd=temp/'snapshot',timeout=conf['build'].get('timeout_s',90))
                (temp/'build.stdout').write_text(r['stdout']); (temp/'build.stderr').write_text(r['stderr'])
                if r['status']!='ok': raise HarnessError('Build failed: '+r['stderr'])
                m={'schema_version':1,'artifact_id':aid,'identity':identity,'created_at':now(),'binary_sha256':sha(temp/'program'),
                   'git_commit':S.git(root,'rev-parse','HEAD'),'workspace_id':digest(str(root))[:12]}
                save(temp/'manifest.json',m); os.replace(temp,dest)
            finally:
                if temp.exists(): shutil.rmtree(temp)
        resolve(root,aid)
    with S.transaction(root) as p:
        key=refkey(root,name); old=p['refs'].get(key)
        if old and re.match(r'^[0-9]{3}_',name) and old['artifact_id']!=aid:
            raise HarnessError('Versioned solver refs are immutable; use a new version name')
        if not old or old['artifact_id']!=aid: p['refs'][key]={'artifact_id':aid,'updated_at':now(),'scope':'workspace'}
    return {'artifact_id':aid,'name':name,'scope':'workspace','path':str(dest)}

def adopt(root,name,artifact,expected,reason,evidence):
    from .research import check_permission
    if name not in PROTECTED-{'submitted'} or not reason or not evidence: raise HarnessError('Adoption requires allowed name, reason and evidence')
    S.require_integrator(root,'adopt'); check_permission(root,'local_adopt')
    a=resolve(root,artifact)
    with S.transaction(root) as p:
        old=p['refs'].get(name,{}).get('artifact_id')
        if old!=expected: raise HarnessError('Shared best changed; compare again against the current artifact')
        p['refs'][name]={'artifact_id':a['artifact_id'],'reason':reason,'evidence':evidence,'updated_at':now()}
        S.event(p,'adopted',name=name,artifact=a['artifact_id'],previous=old,reason=reason,evidence=evidence)
    return p['refs'][name]
