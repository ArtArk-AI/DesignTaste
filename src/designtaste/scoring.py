"""Validation and local aggregation for model submissions."""
from __future__ import annotations
from typing import Any
from .metrics import weighted_total
from .schema import dimensions

def validate_score(record: dict[str,Any], track: str) -> dict[str,Any]:
    ids=dimensions(track); scores=record.get("scores")
    if not isinstance(scores,dict) or set(scores)!=set(ids): raise ValueError(f"scores must contain {ids}")
    if any(type(scores[i]) is not int or not 0<=scores[i]<=10 for i in ids): raise ValueError("scores must be integer 0..10")
    return {"scores":scores,"total":weighted_total(scores,track=track),"rationales":record.get("rationales",{})}
