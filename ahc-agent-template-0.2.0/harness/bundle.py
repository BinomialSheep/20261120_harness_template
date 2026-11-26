"""Conservative quoted-include expansion. No regex minification.
Preserves conditional inclusion and ordinary guard macros. Cycles/macro includes
are rejected. This is not a full preprocessor and has an explicit support scope.
"""
from pathlib import Path
import re,hashlib
from .util import HarnessError,contained,sha,save

def code_mask(s:str)->str:
    """Blank comments and literals, preserving positions/newlines for directives."""
    out=list(s);i=0;n=len(s)
    def blank(a,b):
        for j in range(a,b):
            if s[j]!='\n':out[j]=' '
    while i<n:
        if s.startswith('//',i):
            end=s.find('\n',i);end=n if end<0 else end;blank(i,end);i=end
        elif s.startswith('/*',i):
            end=s.find('*/',i+2)
            if end<0:raise HarnessError('Unterminated comment')
            end+=2;blank(i,end);i=end
        elif s.startswith('R"',i):
            m=re.match(r'R"([^ ()\\\t\r\n]{0,16})\(',s[i:])
            if not m:raise HarnessError('Unsupported raw literal')
            end=s.find(')'+m[1]+'"',i+m.end())
            if end<0:raise HarnessError('Unterminated raw literal')
            end+=len(m[1])+2;blank(i,end);i=end
        elif s[i] in ('"',"'"):
            # C++ digit separators such as 1'000 are not character literals.
            if s[i]=="'" and i>0 and i+1<n and s[i-1].isalnum() and s[i+1].isalnum():i+=1;continue
            quote=s[i];j=i+1
            while j<n:
                if s[j]=='\\':j+=2;continue
                if s[j]==quote:j+=1;break
                j+=1
            blank(i,min(j,n));i=j
        else:i+=1
    return ''.join(out)

def expand(entry:Path,root:Path,stack=())->str:
    entry=contained(root,entry)
    if entry in stack:raise HarnessError('Cyclic local includes: provide a game-specific packer')
    text=entry.read_text(encoding='utf-8')
    # Escaped newlines may join directives/comments. Refuse rather than guess.
    if re.search(r'\\\r?\n',text):raise HarnessError('Line splicing unsupported by the conservative packer')
    masked=code_mask(text).splitlines(keepends=True);lines=text.splitlines(keepends=True)
    pragma=any(re.match(r'^\s*#\s*pragma\s+once\s*$',x.strip()) for x in masked)
    guard='ARENA_BUNDLE_'+hashlib.sha256(str(entry.relative_to(root)).encode()).hexdigest()[:24].upper()
    out=[f'#ifndef {guard}\n#define {guard}\n'] if pragma else []
    for raw,mask in zip(lines,masked):
        if re.match(r'^\s*#\s*pragma\s+once\s*$',mask.strip()):continue
        if re.match(r'^\s*#\s*include_next\b',mask):raise HarnessError('include_next unsupported')
        if re.match(r'^\s*#\s*include\b',mask):
            m=re.match(r'^\s*#\s*include\s*"([^"\n]+)"',raw)
            if m:
                name=m[1]
                if Path(name).is_absolute():raise HarnessError('Absolute includes cannot be packaged')
                p=entry.parent/name
                if not p.exists():p=root/name
                out.append('\n'+expand(p,root,(*stack,entry))+'\n');continue
            if not re.match(r'^\s*#\s*include\s*<[^>]+>',raw):raise HarnessError('Macro/continued includes unsupported')
        out.append(raw)
    if pragma:out.append('\n#endif\n')
    return ''.join(out)

def pack(artifact:dict,out:Path)->dict:
    root=Path(artifact['directory'])/'snapshot';entry=root/artifact['identity']['source']
    text=expand(entry,root)
    out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():raise HarnessError('Refusing to overwrite existing submission file')
    out.write_text(text,encoding='utf-8')
    m={'schema_version':1,'source_artifact_id':artifact['artifact_id'],'sha256':sha(out),
       'utf8_bytes':len(text.encode()),'unicode_codepoints':len(text),'utf16_code_units':len(text.encode('utf-16-le'))//2,
       'minified':False,'verification':'not_yet_compiled_or_equivalence_checked'}
    save(out.with_suffix(out.suffix+'.manifest.json'),m);return m
