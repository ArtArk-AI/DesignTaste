#!/usr/bin/env python3
"""Export score records and paired summaries to a portable workbook."""
from __future__ import annotations
import argparse, csv, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from designtaste.io import read_csv, read_jsonl

def main():
 p=argparse.ArgumentParser(description=__doc__); p.add_argument('--scores',type=Path,required=True); p.add_argument('--summary',type=Path); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
 try:
  import xlsxwriter
 except ImportError: raise SystemExit('Install xlsxwriter to export Excel files')
 wb=xlsxwriter.Workbook(str(a.output));
 for name,path in [('scores',a.scores),('summary',a.summary)]:
  if not path: continue
  rows=read_jsonl(path) if path.suffix=='.jsonl' else read_csv(path)
  if not rows: continue
  ws=wb.add_worksheet(name); fields=list(rows[0]);
  for c,k in enumerate(fields): ws.write(0,c,k)
  for r,row in enumerate(rows,1):
   for c,k in enumerate(fields):
    value=row.get(k,''); ws.write(r,c,str(value) if isinstance(value,(dict,list)) else value)
  ws.freeze_panes(1,0); ws.autofilter(0,0,len(rows),len(fields)-1)
 wb.close(); print(a.output)
if __name__=='__main__': main()
