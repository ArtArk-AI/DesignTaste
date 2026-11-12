"""Dataset and rubric schemas used by the public scripts."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .io import read_csv, read_jsonl, sha256_text

POSTER_DIMENSIONS=("brief_communication","conceptual_originality","visual_organization","aesthetic_expression","formal_resolution")
ADC_DIMENSIONS=("brief_fit","concept_strength","category_fit","visual_craft","visible_communication")
DEFAULT_WEIGHTS=(2.0,3.0,1.5,2.0,1.5)

def dimensions(track: str) -> tuple[str,...]:
    if track=="poster": return POSTER_DIMENSIONS
    if track=="adc": return ADC_DIMENSIONS
    raise ValueError(f"unknown track: {track}")

def load_rubric(path: str | Path) -> dict[str,Any]:
    data=json.loads(Path(path).read_text(encoding="utf-8"))
    ds=data.get("dimensions")
    if not isinstance(ds,list) or not ds: raise ValueError("rubric must contain dimensions")
    ids=[d.get("id") for d in ds]
    if len(ids)!=len(set(ids)): raise ValueError("rubric dimension IDs must be unique")
    return data

def load_tasks(root: str | Path, track: str) -> list[dict[str,str]]:
    return read_csv(Path(root)/"data/manifests"/(track+".csv"))

def load_briefs(root: str | Path, track: str) -> dict[str,dict[str,Any]]:
    return {str(row["task_id"]):row for row in read_jsonl(Path(root)/"data/briefs"/(track+".jsonl"))}

def validate_briefs(rows: list[dict[str,Any]]) -> None:
    for row in rows:
        text=row.get("brief","").strip()
        if not row.get("task_id") or not text: raise ValueError("each brief needs task_id and non-empty brief")
        if row.get("brief_sha256") and row["brief_sha256"]!=sha256_text(text): raise ValueError(f"brief checksum mismatch: {row['task_id']}")
