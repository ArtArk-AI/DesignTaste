"""Aggregation and paired-comparison metrics."""
from __future__ import annotations
from collections import Counter
from statistics import mean, median, stdev
from typing import Any, Iterable
from .schema import DEFAULT_WEIGHTS, dimensions

def weighted_total(scores: dict[str,float], weights: Iterable[float]=DEFAULT_WEIGHTS, track: str|None=None) -> float:
    ids=list(scores) if track is None else list(dimensions(track))
    ws=list(weights)
    if len(ids)!=len(ws): raise ValueError("weights and scores must have the same length")
    if any(k not in scores for k in ids): raise ValueError("missing score dimension")
    return round(sum(float(scores[k])*w for k,w in zip(ids,ws))*10/sum(ws),2)

def paired_deltas(reference: dict[str,float], candidate: dict[str,float]) -> list[float]:
    keys=sorted(set(reference)&set(candidate)); return [float(candidate[k])-float(reference[k]) for k in keys]

def summarize_pairs(rows: list[dict[str,Any]], track: str, reference="reference") -> list[dict[str,Any]]:
    by={}
    for row in rows: by.setdefault(str(row["task_id"]),{})[row["source"]]=row
    out=[]
    for source in sorted({r["source"] for r in rows}-{reference}):
        pairs=[(v[reference],v[source]) for v in by.values() if reference in v and source in v]
        if not pairs: continue
        deltas=[float(c["total"])-float(h["total"]) for h,c in pairs]
        out.append({"track":track,"source":source,"n":len(deltas),"mean":round(mean([float(c["total"]) for _,c in pairs]),3),
                    "reference_mean":round(mean([float(h["total"]) for h,_ in pairs]),3),"delta":round(mean(deltas),3),
                    "higher":sum(d>0 for d in deltas),"equal":sum(d==0 for d in deltas),"lower":sum(d<0 for d in deltas),"median_delta":median(deltas)})
    return out
