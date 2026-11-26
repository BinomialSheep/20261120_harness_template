#!/usr/bin/env python3
"""Apply local Linux/WSL per-process limits, then exec the solver. No threaded preexec_fn."""
import json, os, resource, sys

def main():
    cfg=json.loads(sys.argv[1]);argv=sys.argv[2:]
    if cfg.get('memory_mb'):
        n=int(cfg['memory_mb'])*1024*1024;resource.setrlimit(resource.RLIMIT_AS,(n,n))
    if cfg.get('cpu_s'):
        n=int(cfg['cpu_s']);resource.setrlimit(resource.RLIMIT_CPU,(n,n+1))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    os.execvpe(argv[0],argv,os.environ)
if __name__=='__main__':main()
