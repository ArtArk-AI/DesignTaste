#!/usr/bin/env python3
"""Review briefs for factual grounding and usable creative scope."""
from __future__ import annotations
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from designtaste.io import read_csv, read_jsonl, write_jsonl
from designtaste.providers import LANGUAGE_MODELS, ModelService

def main():
 p=argparse.ArgumentParser(description=__doc__); p.add_argument('--track',choices=('poster','adc'),required=True); p.add_argument('--manifest',type=Path,required=True); p.add_argument('--briefs',type=Path,required=True); p.add_argument('--prompt',type=Path,required=True); p.add_argument('--images',type=Path); p.add_argument('--output',type=Path,required=True); p.add_argument('--model',choices=LANGUAGE_MODELS,default='gpt-6-luna'); p.add_argument('--workers',type=int,default=8); p.add_argument('--max-output-tokens',type=int,default=500); p.add_argument('--regenerate-failures',action='store_true')
 a=p.parse_args(); manifest={str(r['task_id']):r for r in read_csv(a.manifest)}; briefs={str(r['task_id']):r for r in read_jsonl(a.briefs)}; template=a.prompt.read_text(encoding='utf-8')
 def one(task,row):
  m=manifest[task]; image=[]
  if a.images:
   path=a.images/m['reference_filename'];
   if path.is_file(): image=[path]
  prompt=template.format(work_title=m.get('title',''),title=m.get('title',''),client=m.get('client',''),discipline=m.get('discipline',''),category=m.get('category',m.get('full_category','')),context_kind=m.get('context_kind',''),context=row.get('brief',''),brief=row.get('brief',''))
  with ModelService() as service: text=service.generate_text(prompt,model=a.model,images=image,max_output_tokens=a.max_output_tokens).strip()
  verdict='pass' if 'pass' in text.lower() and 'fail' not in text.lower() else ('fail' if 'fail' in text.lower() else 'review')
  return dict(row,review_verdict=verdict,review_text=text)
 out=[]
 with ThreadPoolExecutor(max_workers=max(1,a.workers)) as pool:
  futures={pool.submit(one,k,v):k for k,v in briefs.items()}
  for f in as_completed(futures): out.append(f.result())
 write_jsonl(a.output,sorted(out,key=lambda x:int(x['task_id']) if x['task_id'].isdigit() else x['task_id']))
 print(f'wrote {len(out)} reviews to {a.output}')
if __name__=='__main__': main()
