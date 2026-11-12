from pathlib import Path
import importlib.util
import sys
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from designtaste.io import read_csv, read_jsonl
from designtaste.metrics import weighted_total
from designtaste.schema import dimensions

def test_public_counts():
    root=Path(__file__).parents[1]
    assert len(read_csv(root/'data/manifests/poster.csv'))==100
    assert len(read_csv(root/'data/manifests/adc.csv'))==303
    assert len(read_jsonl(root/'data/briefs/poster.jsonl'))==100
    assert len(read_jsonl(root/'data/briefs/adc.jsonl'))==303

def test_weighted_total():
    assert weighted_total(dict.fromkeys(dimensions('poster'),10),track='poster')==100

def test_reference_paths_from_released_manifests(tmp_path):
    root=Path(__file__).parents[1]
    spec=importlib.util.spec_from_file_location('score_images',root/'scripts/score_images.py')
    script=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    for track in ('poster','adc'):
        for row in read_csv(root/'data/manifests'/(track+'.csv')):
            assert script.reference_image_path(tmp_path,row)==tmp_path/row['reference_path']
    assert script.reference_image_path(tmp_path,{'reference_filename':'legacy.png'})==tmp_path/'legacy.png'
    with pytest.raises(ValueError,match='task missing has no reference_path or reference_filename'):
        script.reference_image_path(tmp_path,{'task_id':'missing'})
