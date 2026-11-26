#!/usr/bin/env python3
"""Demo and test worker: copies the variant named in task.md. Not an LLM."""
import re
import shutil
from pathlib import Path

w = Path.cwd()
m = re.search(r'(variant_\w+)', (w / 'task.md').read_text(encoding='utf-8'))
shutil.copyfile(w / f'{m.group(1)}.cpp', w / 'main.cpp')
(w / 'notes.md').write_text(f'## 変更点\n{m.group(1)} を写した\n## 構成\nmain だけ\n## 確かめられなかった点\nなし\n', encoding='utf-8')
print('fake worker:', m.group(1))
