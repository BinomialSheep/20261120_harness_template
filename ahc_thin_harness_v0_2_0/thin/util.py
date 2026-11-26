"""Small helpers shared by the harness. Standard library only."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


class ThinError(Exception):
    """A user-facing error: printed without a traceback."""


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for part in iter(lambda: f.read(1 << 20), b''):
            h.update(part)
    return h.hexdigest()


def write_text(path, text):
    """Atomic write: a crash leaves either the old or the new file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.tmp-', delete=False) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(f.name, path)


def write_json(path, obj):
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as e:
        raise ThinError(f'JSON を読めない: {path}: {e}') from e


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_id(s):
    if not isinstance(s, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,80}', s):
        raise ThinError(f'ID が不正: {s}')
    return s


def relpath(root, ref):
    p = (Path(root) / ref).resolve()
    if not p.is_relative_to(Path(root).resolve()):
        raise ThinError(f'プロジェクトの外を指している: {ref}')
    return p


def format_argv(argv, mapping):
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv):
        raise ThinError('コマンドは文字列の JSON 配列で書く（シェルの文字列は使えない）')
    try:
        return [x.format_map(mapping) for x in argv]
    except (ValueError, KeyError, IndexError) as e:
        raise ThinError(f'コマンドの置換 {{{e}}} が使えない: {argv}') from e


def root_from(start=None):
    p = Path(start or os.getcwd()).resolve()
    for base in [p, *p.parents]:
        if (base / 'config.json').is_file() and (base / 'thin' / '__init__.py').is_file():
            return base
    raise ThinError('config.json と thin/ のあるプロジェクトの中で実行するか、--root を付ける')


def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in str(s))


def table(head, rows, right=()):
    """Plain-text table for the terminal. right: column indexes aligned to the right."""
    rows = [[str(c) for c in r] for r in rows]
    w = [max([width(h)] + [width(r[i]) for r in rows]) for i, h in enumerate(head)]

    def line(cells):
        out = []
        for i, c in enumerate(cells):
            pad = ' ' * (w[i] - width(c))
            out.append(pad + c if i in right else c + pad)
        return '  '.join(out).rstrip()
    return '\n'.join([line(head), line(['-' * x for x in w])] + [line(r) for r in rows])


def fmt_effect(x, se=None, digits=3):
    if x is None:
        return '—'
    s = f'{x:+.{digits}f}'.replace('-', '−')
    return s + (f' ± {se:.{digits}f}' if se else '')
