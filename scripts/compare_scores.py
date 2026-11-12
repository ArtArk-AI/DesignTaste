#!/usr/bin/env python3
"""Compare model scores with the award-winning reference within each task."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from designtaste.io import read_jsonl, read_csv, write_csv
from designtaste.metrics import summarize_pairs, weighted_total
from designtaste.schema import dimensions, DEFAULT_WEIGHTS

def main():
 p=argparse.ArgumentParser(description=__doc__); p.add_argument('--track',choices=('poster','adc'),required=True); p.add_argument('--scores',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--weights',default='2:3:1.5:2:1.5'); a=p.parse_args()
 weights=tuple(float(x) for x in a.weights.split(':')); ids=dimensions(a.track); rows=[]
 raw = read_jsonl(a.scores) if a.scores.suffix == '.jsonl' else read_csv(a.scores)
 for r in raw:
  r=dict(r)
  if 'scores' in r and isinstance(r['scores'], dict): scores=r['scores']
  else:
   ids=dimensions(a.track); scores={k:int(r[k]) for k in ids}
  r['total']=weighted_total(scores,weights,track=a.track); rows.append(r)
 result=summarize_pairs(rows,a.track); write_csv(a.output,result); print(f'wrote {len(result)} model summaries to {a.output}')
if __name__=='__main__': main()
