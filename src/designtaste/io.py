"""Small, dependency-free readers and writers for benchmark files."""
from __future__ import annotations
import csv, hashlib, json
from pathlib import Path
from typing import Any, Iterable

def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p=Path(path)
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("w",encoding="utf-8") as f:
        for row in rows: f.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+"\n")

def read_csv(path: str | Path) -> list[dict[str,str]]:
    with Path(path).open(encoding="utf-8-sig",newline="") as f: return list(csv.DictReader(f))

def write_csv(path: str | Path, rows: list[dict[str,Any]], fields: list[str] | None = None) -> None:
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    if fields is None: fields=list(rows[0]) if rows else []
    with p.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

def sha256_file(path: str | Path) -> str:
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""): h.update(block)
    return h.hexdigest()

def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
