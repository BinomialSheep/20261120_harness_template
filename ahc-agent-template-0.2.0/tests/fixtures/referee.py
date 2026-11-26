"""Synthetic closed-loop tester. Owns the secret and enforces per-turn response deadlines."""
import json,os,select,subprocess,sys,time
from pathlib import Path
source,solver,result=sys.argv[1:4];secret=int(Path(source).read_text())
proc=subprocess.Popen([solver],stdin=subprocess.PIPE,stdout=subprocess.PIPE,bufsize=0)
transcript=[];pending=b'';queries=0;start=time.monotonic()
def send(s):
    transcript.append({'to':'solver','text':s});proc.stdin.write((s+'\n').encode());proc.stdin.flush()
def receive(timeout=.5):
    global pending
    end=time.monotonic()+timeout
    while b'\n' not in pending:
        if time.monotonic()>=end:raise TimeoutError('turn timeout / missing flush')
        rs,_,_=select.select([proc.stdout],[],[],max(0,end-time.monotonic()))
        if not rs:continue
        b=os.read(proc.stdout.fileno(),4096)
        if not b:raise ChildProcessError('solver EOF')
        pending+=b
        if len(pending)>65536:raise ValueError('oversized response')
    line,pending=pending.split(b'\n',1);s=line.decode();transcript.append({'to':'tester','text':s});return s
try:
    send('SEARCH_V1 0 255 512')
    while True:
        op,val=receive().split();x=int(val)
        if op=='?':
            queries+=1
            if queries>512 or not 0<=x<=255:raise ValueError('invalid query')
            send('=' if x==secret else '<' if secret<x else '>')
        elif op=='!':
            if x!=secret:raise ValueError('wrong final answer')
            r={'schema_version':1,'status':'ok','valid':True,'raw_score':1000-queries,
               'metrics':{'queries':queries,'solver_wall_s':time.monotonic()-start}}
            break
        else:raise ValueError('protocol')
except TimeoutError as e:r={'schema_version':1,'status':'solver_timeout','valid':False,'raw_score':None,'reason':str(e)}
except ChildProcessError as e:r={'schema_version':1,'status':'solver_crash','valid':False,'raw_score':None,'reason':str(e)}
except Exception as e:r={'schema_version':1,'status':'solver_invalid','valid':False,'raw_score':None,'reason':str(e)}
finally:
    if proc.poll() is None:proc.kill()
    proc.wait();proc.stdin.close();proc.stdout.close()
Path(result).write_text(json.dumps(r)+'\n')
Path('transcript.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in transcript))
