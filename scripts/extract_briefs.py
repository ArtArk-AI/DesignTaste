#!/usr/bin/env python3
"""Extract open-ended design briefs from reference images or award context."""
from __future__ import annotations
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from designtaste.io import read_csv, sha256_text, write_jsonl
from designtaste.providers import LANGUAGE_MODELS, ModelService

def render(template, row, context=''):
    values={'work_title':row.get('title',row.get('task_title','')),'title':row.get('title',''),'client':row.get('client',''),'discipline':row.get('discipline',''),'category':row.get('category',row.get('full_category','')),'context_kind':row.get('context_kind',''),'context':context}
    return template.format(**values)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--track',choices=('poster','adc'),required=True); p.add_argument('--manifest',type=Path,required=True); p.add_argument('--images',type=Path,help='Reference image directory (poster track)'); p.add_argument('--context-json',type=Path,help='JSON object mapping ADC task_id to context text'); p.add_argument('--prompt',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--model',choices=LANGUAGE_MODELS,default='gpt-6-luna'); p.add_argument('--workers',type=int,default=8); p.add_argument('--max-output-tokens',type=int,default=1200); p.add_argument('--attempts',type=int,default=2); p.add_argument('--limit',type=int)
    a=p.parse_args(); rows=read_csv(a.manifest); rows=rows[:a.limit] if a.limit else rows
    contexts=json.loads(a.context_json.read_text(encoding='utf-8')) if a.context_json else {}; template=a.prompt.read_text(encoding='utf-8')
    def one(row):
        task=str(row['task_id']); image=[]
        if a.images:
            path=a.images/row['reference_filename'];
            if not path.is_file(): raise FileNotFoundError(path)
            image=[path]
        prompt=render(template,row,str(contexts.get(task,'')))
        with ModelService() as service:
            for _ in range(a.attempts):
                text=service.generate_text(prompt,model=a.model,images=image,max_output_tokens=a.max_output_tokens).strip()
                if text: break
        if text.startswith('```') and text.endswith('```'): text='\n'.join(text.splitlines()[1:-1]).strip()
        return {'task_id':task,'reference_filename':row['reference_filename'],'brief':text,'brief_sha256':sha256_text(text),'model':a.model}
    out=[]; errors=[]
    with ThreadPoolExecutor(max_workers=max(1,a.workers)) as pool:
        futures={pool.submit(one,r):r for r in rows}
        for f in as_completed(futures):
            try: out.append(f.result()); print('brief',futures[f]['task_id'])
            except Exception as e: errors.append((futures[f]['task_id'],str(e))); print('error',futures[f]['task_id'],e,file=sys.stderr)
    if errors: raise SystemExit(f'{len(errors)} tasks failed')
    write_jsonl(a.output,sorted(out,key=lambda x:int(x['task_id']) if x['task_id'].isdigit() else x['task_id']))
    print(f'wrote {len(out)} briefs to {a.output}')
if __name__=='__main__': main()
