"""Command-line entry points for offline benchmark analysis."""
from __future__ import annotations
import argparse
from .analysis import compare_scores

def compare() -> None:
 p=argparse.ArgumentParser(description='Compare model scores with award-winning references')
 p.add_argument('--track',choices=('poster','adc'),required=True); p.add_argument('--scores',required=True); p.add_argument('--output',required=True)
 a=p.parse_args(); compare_scores(a.scores,a.track,a.output)
