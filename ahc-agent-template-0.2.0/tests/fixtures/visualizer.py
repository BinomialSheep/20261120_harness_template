"""Synthetic SVG selection view. No browser automation and no network."""
import sys,html
from pathlib import Path
x=list(map(int,Path(sys.argv[1]).read_text().split()));chosen=set(map(int,Path(sys.argv[2]).read_text().split()))
n,k,salt=x[:3];a=x[3:];body=['<svg xmlns="http://www.w3.org/2000/svg" width="700" height="200">']
for i,v in enumerate(a):
    h=v/10;body.append(f'<rect x="{10+i*55}" y="{150-h}" width="40" height="{h}" fill="{"#255f85" if i in chosen else "#bcc7ce"}"/>')
    body.append(f'<text x="{10+i*55}" y="175">{i}: {v}</text>')
body.append('</svg>');Path(sys.argv[3]).write_text(''.join(body))
