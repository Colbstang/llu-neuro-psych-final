#!/usr/bin/env python3
"""Prepare ignored local Anki and PDF reference indexes for a private build."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DEFAULT_RUNTIME=ROOT/'local-private'/'runtime'

def run(*args: str) -> None:
    subprocess.run([sys.executable,*args],check=True)

def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',type=Path,default=DEFAULT_RUNTIME)
    args=parser.parse_args()
    runtime=args.runtime.resolve()
    if not (runtime/'index.html').is_file():
        raise SystemExit('Private runtime is missing. Run python3 build_local.py first.')
    helper=runtime/'prepare_reference_catalog.py'
    backend=runtime/'semantic_search.py'
    if not helper.is_file() or not backend.is_file():
        raise SystemExit('Private search modules are missing. Run python3 build_local.py again.')

    run(str(helper),'--root',str(runtime))
    catalog=json.loads((runtime/'reference_index'/'catalog.json').read_text())
    books=catalog.get('books',{})
    ready=all(bool(books.get(name,{}).get('available')) for name in ('First Aid','Pathoma'))
    if ready:
        run(str(helper),'--root',str(runtime),'--index')
    else:
        print('First Aid and Pathoma were not both found. Exact imported PDF pages remain available; book passage search will be inactive until both local PDFs are imported.',flush=True)

    run(str(backend),'--prepare')
    data=json.loads((runtime/'anki_context_data.js').read_text().split('=',1)[1].rstrip().removesuffix(';'))
    print(f"Local indexes ready: {len(data.get('notes',[]))} notes, {len(data.get('cards',[]))} cards. To run the app, serve local-private/runtime on port 8767 and start its semantic_search.py on port 8768.",flush=True)

if __name__=='__main__':main()
