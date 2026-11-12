#!/usr/bin/env python3
"""Score reference and generated images with the benchmark rubric."""
from __future__ import annotations
import argparse, json, re, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from designtaste.io import read_csv, read_jsonl, sha256_file, write_jsonl
from designtaste.providers import LANGUAGE_MODELS, ModelService
from designtaste.schema import dimensions, load_rubric

def parse_json(text):
 text=text.strip()
 match=re.search(r'\{.*\}',text,re.S)
 if not match: raise ValueError('model response did not contain JSON')
 return json.loads(match.group(0))

def reference_image_path(reference_images, manifest_row):
 path=manifest_row.get('reference_path') or manifest_row.get('reference_filename')
 if not path:
  raise ValueError(f"task {manifest_row.get('task_id', '<unknown>')} has no reference_path or reference_filename")
 return reference_images/path

def main():
 p=argparse.ArgumentParser(description=__doc__); p.add_argument('--track',choices=('poster','adc'),required=True); p.add_argument('--manifest',type=Path,required=True); p.add_argument('--briefs',type=Path,required=True); p.add_argument('--rubric',type=Path,required=True); p.add_argument('--prompt',type=Path,required=True); p.add_argument('--reference-images',type=Path,required=True); p.add_argument('--generated-dir',type=Path); p.add_argument('--model-source',default='artark',help='Model label for generated images; omit to score only references'); p.add_argument('--include-reference',action='store_true'); p.add_argument('--judge-model',choices=LANGUAGE_MODELS,default='gpt-6-luna'); p.add_argument('--output',type=Path,required=True); p.add_argument('--workers',type=int,default=8); p.add_argument('--max-output-tokens',type=int,default=1800); p.add_argument('--attempts',type=int,default=2); p.add_argument('--limit',type=int)
 a=p.parse_args(); manifest={str(r['task_id']):r for r in read_csv(a.manifest)}; briefs={str(r['task_id']):r for r in read_jsonl(a.briefs)}; rubric=load_rubric(a.rubric); template=a.prompt.read_text(encoding='utf-8'); ids=dimensions(a.track)
 sources=[]
 if a.include_reference: sources.append(('reference',None))
 if a.generated_dir: sources.append((a.model_source,a.generated_dir))
 if not sources: raise SystemExit('select --include-reference and/or --generated-dir')
 tasks=list(manifest)[:a.limit] if a.limit else list(manifest)
 def one(task,source,gen_dir):
  m=manifest[task]; b=briefs[task]
  image=reference_image_path(a.reference_images,m) if source=='reference' else (gen_dir/(task+'.png'))
  if not image.is_file(): raise FileNotFoundError(image)
  values={'brief':b['brief'],'discipline':m.get('discipline',''),'category':m.get('category',m.get('full_category','')),'title':m.get('title',''),'client':m.get('client',''),'context':m.get('context',''),'context_kind':m.get('context_kind',''),'work_title':m.get('title',''),'rubric':json.dumps(rubric,ensure_ascii=False)}
  prompt=template.format(**values)
  with ModelService() as service:
   last=None
   for _ in range(a.attempts):
    try:
     result=parse_json(service.generate_text(prompt,model=a.judge_model,images=[image],max_output_tokens=a.max_output_tokens)); scores=result.get('scores',{})
     if set(scores)!=set(ids) or any(type(scores[k]) is not int or not 0<=scores[k]<=10 for k in ids): raise ValueError('invalid score dimensions')
     return {'track':a.track,'task_id':task,'source':source,'judge':a.judge_model,'brief_sha256':b.get('brief_sha256',''),'image_sha256':sha256_file(image),'scores':scores,'rationales':result.get('rationales',{}),'overall_note':result.get('overall_note','')}
    except Exception as e: last=e
  raise last
 out=[]; errors=[]
 work=[(task,src,gd) for task in tasks for src,gd in sources]
 with ThreadPoolExecutor(max_workers=max(1,a.workers)) as pool:
  futures={pool.submit(one,*item):item for item in work}
  for f in as_completed(futures):
   try: out.append(f.result()); print(futures[f][0],futures[f][1])
   except Exception as e: errors.append((futures[f][0],str(e))); print('error',futures[f],e,file=sys.stderr)
 if errors: raise SystemExit(f'{len(errors)} scores failed')
 write_jsonl(a.output,sorted(out,key=lambda x:(int(x['task_id']) if x['task_id'].isdigit() else x['task_id'],x['source'])))
 print(f'wrote {len(out)} scores to {a.output}')
if __name__=='__main__': main()
