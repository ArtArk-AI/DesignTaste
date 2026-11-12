#!/usr/bin/env python3
"""Generate one image per brief with a selected model adapter."""
from __future__ import annotations
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from designtaste.io import read_jsonl, write_jsonl
from designtaste.providers import (ARTARK_MODEL, ARTARK_TIERS, AZURE_IMAGE_MODELS, NANO_BANANA_PRO_MODEL, VOLCENGINE_SEEDREAM_MODEL, ArtArkService, BaiduNanoBananaProService, ModelService, VolcengineSeedreamService)

def main():
 p=argparse.ArgumentParser(description=__doc__); p.add_argument('--briefs',type=Path,required=True); p.add_argument('--prompt',type=Path,required=True); p.add_argument('--model',required=True); p.add_argument('--output-dir',type=Path,required=True); p.add_argument('--records',type=Path,required=True); p.add_argument('--tier',choices=ARTARK_TIERS,default='L3'); p.add_argument('--size',default='auto'); p.add_argument('--quality',default=None); p.add_argument('--workers',type=int,default=4); p.add_argument('--limit',type=int)
 a=p.parse_args(); rows=read_jsonl(a.briefs); rows=rows[:a.limit] if a.limit else rows; template=a.prompt.read_text(encoding='utf-8')
 def one(row):
  task=str(row['task_id']); prompt=template.format(brief=row['brief'],orientation='portrait',ratio=1.0,discipline=row.get('discipline',''),category=row.get('category',''),title=row.get('title',''),client=row.get('client',''),context=row.get('context',''),context_kind=row.get('context_kind',''))
  out=a.output_dir/(task+'.png'); out.parent.mkdir(parents=True,exist_ok=True)
  if out.exists(): return {'task_id':task,'model':a.model,'image':str(out),'status':'exists'}
  if a.model==ARTARK_MODEL:
   with ArtArkService() as service: paths=service.generate_images(prompt,out,tier=a.tier,client_request_id=f'designtaste-{task}-{a.tier.lower()}')
   image=paths[0]
  elif a.model in AZURE_IMAGE_MODELS:
   with ModelService() as service: image=service.generate_image(prompt,out,model=a.model,size=a.size,quality=a.quality)
  elif a.model==NANO_BANANA_PRO_MODEL:
   with BaiduNanoBananaProService() as service: image=service.generate_image(prompt,out,size=None if a.size=='auto' else a.size,quality=a.quality)
  elif a.model==VOLCENGINE_SEEDREAM_MODEL:
   with VolcengineSeedreamService() as service: image=service.generate_image(prompt,out,size=a.size)
  else: raise ValueError('unknown model: '+a.model)
  return {'task_id':task,'model':a.model,'image':str(image),'status':'generated'}
 out=[]; errors=[]
 with ThreadPoolExecutor(max_workers=max(1,a.workers)) as pool:
  futures={pool.submit(one,r):r for r in rows}
  for f in as_completed(futures):
   try: out.append(f.result()); print(out[-1]['task_id'])
   except Exception as e: errors.append(str(e)); print('error',e,file=sys.stderr)
 if errors: raise SystemExit(f'{len(errors)} generations failed')
 write_jsonl(a.records,sorted(out,key=lambda x:int(x['task_id']) if x['task_id'].isdigit() else x['task_id']))
 print(f'wrote {len(out)} records to {a.records}')
if __name__=='__main__': main()
