"""Run a command with a time limit and clean up what it leaves behind. Not a security sandbox."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from .util import ThinError


def execute(argv, cwd, seconds, stdin=None, stdout_file=None, stderr_file=None, env=None):
    """Run argv in its own process group; kill the whole group at the time limit."""
    if seconds <= 0:
        raise ThinError('制限時間は正の数にする')
    cwd = Path(cwd)
    out = Path(stdout_file) if stdout_file else cwd / '_stdout.txt'
    err = Path(stderr_file) if stderr_file else cwd / '_stderr.txt'
    out.parent.mkdir(parents=True, exist_ok=True)
    err.parent.mkdir(parents=True, exist_ok=True)
    t = time.monotonic()
    try:
        with open(out, 'wb') as o, open(err, 'wb') as e:
            p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                 stdout=o, stderr=e, start_new_session=True, env=env)
            data = stdin.encode('utf-8') if isinstance(stdin, str) else stdin
            try:
                p.communicate(input=data, timeout=seconds)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                _killpg(p.pid)
                p.communicate()
            except BaseException:
                _killpg(p.pid)
                p.communicate()
                raise
            _killpg(p.pid)  # children that outlived the leader in the same group
    except OSError as ex:
        return {'status': 'launch_error', 'error': str(ex), 'seconds': round(time.monotonic() - t, 3),
                'stdout': str(out), 'stderr': str(err)}
    status = 'timeout' if timed_out else ('ok' if p.returncode == 0 else 'error')
    return {'status': status, 'returncode': p.returncode, 'seconds': round(time.monotonic() - t, 3),
            'stdout': str(out), 'stderr': str(err)}


def _killpg(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def processes_in(path):
    """PIDs whose working directory is `path` or below it (Linux /proc). Empty elsewhere."""
    base = str(Path(path).resolve())
    proc = Path('/proc')
    if not proc.is_dir():
        return []
    found = []
    for p in proc.iterdir():
        if not p.name.isdigit() or int(p.name) == os.getpid():
            continue
        try:
            cwd = os.readlink(p / 'cwd')
        except OSError:
            continue
        if cwd == base or cwd.startswith(base + os.sep):
            found.append(int(p.name))
    return found


def kill_in(path, grace=3.0):
    """Stop processes left running in a worker's directory, including detached ones (setsid, nohup)."""
    pids = processes_in(path)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.monotonic() + grace
        while sig == signal.SIGTERM and time.monotonic() < deadline and any(_alive(x) for x in pids):
            time.sleep(0.1)
    return pids


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
