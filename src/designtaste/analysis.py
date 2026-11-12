"""Offline analysis helpers for frozen score files and new submissions."""
from __future__ import annotations
import csv
from pathlib import Path
from .io import read_csv, write_csv
from .metrics import summarize_pairs

def compare_scores(score_file: str|Path, track: str, output: str|Path) -> list[dict]:
    rows=read_csv(score_file)
    for row in rows: row["total"]=float(row["total"])
    result=summarize_pairs(rows,track)
    write_csv(output,result)
    return result
