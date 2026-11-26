#!/usr/bin/env python3
"""For tests/demo only; picks a provided source. Never an actual LLM agent."""
import sys,shutil
from pathlib import Path
w=Path.cwd()
# Every toy worker receives a bundled solution in a different, isolated file.
src=w/'toy_optimized.cpp'
shutil.copyfile(src,w/'main.cpp')
print('synthetic mock worker generated a solution',flush=True)
