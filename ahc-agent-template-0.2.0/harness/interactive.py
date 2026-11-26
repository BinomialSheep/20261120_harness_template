"""Persistent newline I/O transport for tiny/custom games (not CG semantics)."""
from __future__ import annotations
import os, select, subprocess, threading, time
from .process import clean_env

class BotFault(RuntimeError):
    def __init__(self,kind:str,message:str):self.kind=kind;super().__init__(message)

class LineBot:
    def __init__(self,command:list[str],*,cwd,env=None,max_bytes=65536):
        self.max_bytes=max_bytes;self.buf=bytearray();self.err=bytearray();self.overflow=False
        try:self.p=subprocess.Popen(command,cwd=cwd,env=clean_env(env),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        except OSError as e:raise BotFault('crash',str(e)) from e
        os.set_blocking(self.p.stdin.fileno(),False);os.set_blocking(self.p.stdout.fileno(),False)
        def drain():
            try:
                while True:
                    b=self.p.stderr.read(4096)
                    if not b:break
                    room=max_bytes-len(self.err);self.err.extend(b[:max(0,room)])
                    if len(b)>room:self.overflow=True;self.p.kill();break
            except OSError:pass
        self.thread=threading.Thread(target=drain,daemon=True);self.thread.start()
    def exchange(self,observation:str,timeout_s:float)->str:
        data=observation.encode();deadline=time.monotonic()+timeout_s;sent=0
        if len(data)>self.max_bytes:raise BotFault('invalid','Observation exceeds transport limit')
        while sent<len(data):
            left=deadline-time.monotonic()
            if left<=0:raise BotFault('timeout','stdin write timed out')
            _,ready,_=select.select([], [self.p.stdin], [],left)
            if ready:
                try:sent+=os.write(self.p.stdin.fileno(),data[sent:])
                except (BrokenPipeError,OSError) as e:raise BotFault('crash','Bot closed stdin') from e
        while b'\n' not in self.buf:
            if self.overflow:raise BotFault('invalid','stderr limit exceeded')
            left=deadline-time.monotonic()
            if left<=0:raise BotFault('timeout','No complete action line; check flush')
            ready,_,_=select.select([self.p.stdout],[],[],left)
            if not ready:continue
            b=os.read(self.p.stdout.fileno(),4096)
            if not b:raise BotFault('crash','EOF before an action')
            self.buf.extend(b)
            if len(self.buf)>self.max_bytes:raise BotFault('invalid','stdout line limit exceeded')
        line,_,rest=self.buf.partition(b'\n');self.buf=bytearray(rest)
        try:return line.decode('utf-8').rstrip('\r')
        except UnicodeDecodeError as e:raise BotFault('invalid','Non-UTF8 action') from e
    def close(self):
        if self.p.poll() is None:
            self.p.terminate()
            try:self.p.wait(timeout=.2)
            except subprocess.TimeoutExpired:self.p.kill();self.p.wait()
        for f in (self.p.stdin,self.p.stdout):f.close()
        self.thread.join(timeout=.3);self.p.stderr.close()
