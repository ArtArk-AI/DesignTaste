# Reproducing results

Use the released scores to reproduce comparisons without API credentials or image downloads. To evaluate new images, use the released briefs and the scoring workflow below.

## Install

Use **Python 3.10 or newer**. Run the following commands from a terminal:

```bash
git clone https://github.com/ArtArk-AI/DesignTaste.git
cd DesignTaste
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e . -r requirements.txt
```

Run the remaining commands from the repository root.

## Reproduce the released comparisons

Recompute the Poster and ADC summaries from the frozen image scores:

```bash
python scripts/compare_scores.py \
  --track poster \
  --scores data/annotations/poster_scores.csv \
  --weights 2:3:1.5:2:1.5 \
  --output runs/poster_summary.csv

python scripts/compare_scores.py \
  --track adc \
  --scores data/annotations/adc_scores.csv \
  --weights 2:3:1.5:2:1.5 \
  --output runs/adc_summary.csv
```

Each summary reports model and award-winning mean scores, their differences, and the number of tasks where the model scores higher, ties, or scores lower. Overall scores use a 0-100 scale.

The weight order is **Thematic Expression, Creative Originality, Visual Organization, Aesthetic Expression, Formal Completion**. Change `--weights` to explore another weighting without generating or scoring images again. The released dimension scores remain unchanged.

### Export to Excel

After computing the summaries, export each track's image scores and summary to a workbook:

```bash
python scripts/export_results.py \
  --scores data/annotations/poster_scores.csv \
  --summary runs/poster_summary.csv \
  --output runs/poster_results.xlsx

python scripts/export_results.py \
  --scores data/annotations/adc_scores.csv \
  --summary runs/adc_summary.csv \
  --output runs/adc_results.xlsx
```

These workbooks contain the input scores and computed summaries. For the paper's consolidated tables, use [DesignTaste_results.xlsx](../data/results/DesignTaste_results.xlsx).

## Evaluate new model outputs

Use [the released briefs](../data/briefs/) when comparing a new model with the reported results. Generate one PNG per task, named `<task_id>.png`, in a separate directory for each model. Keep the task IDs and briefs unchanged. Any model can participate by producing these files; a bundled adapter is optional.

The full reference-image collections are external to this repository. For scoring, provide a local image root through `--reference-images`. The paths in the corresponding [manifest](../data/manifests/) are resolved relative to that root. See [the dataset guide](DATASET.md) for the file layout and [submission instructions](SUBMISSIONS.md) for model records.

### Configure API access

Export the variables needed by the selected providers in your shell. [.env.example](../.env.example) lists the settings; keep credentials in a local, untracked file.

| Provider | Environment variables |
| --- | --- |
| Azure, including the scoring model | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_PROXY` |
| ArtArk | `ARTARK_API_KEY`; optionally `ARTARK_ENDPOINT` |
| Baidu VOD | `BAIDU_VOD_ACCESS_KEY_ID`, `BAIDU_VOD_ACCESS_KEY_SECRET`; optionally `BAIDU_VOD_ENDPOINT` |
| Volcengine | `VOLCENGINE_ARK_API_KEY`; optionally `VOLCENGINE_ARK_ENDPOINT` |

Azure calls use the configured proxy. ArtArk connects directly. The offline comparisons above do not need any of these variables.

### Example: ArtArk on Poster

Generate images with ArtArk L3, using ten workers:

```bash
python scripts/generate_images.py \
  --briefs data/briefs/poster.jsonl \
  --prompt prompts/poster/generate_image.txt \
  --model artark --tier L3 --workers 10 \
  --output-dir runs/poster/artark/images \
  --records runs/poster/artark/generation.jsonl
```

Score the generated images and award-winning works with the released Poster rubric:

```bash
python scripts/score_images.py \
  --track poster \
  --manifest data/manifests/poster.csv \
  --briefs data/briefs/poster.jsonl \
  --rubric data/rubrics/poster.json \
  --prompt prompts/poster/score_image.txt \
  --reference-images /path/to/reference-images \
  --generated-dir runs/poster/artark/images \
  --model-source artark --include-reference \
  --judge-model gpt-6.1-sol --workers 20 \
  --output runs/poster/artark/scores.jsonl
```

Replace `/path/to/reference-images` with your local reference-image root. `--include-reference` scores the award-winning works as well as the generated images, supplying both sides of each comparison. To evaluate your own outputs, change `--generated-dir` and `--model-source`.

Compute the comparison:

```bash
python scripts/compare_scores.py \
  --track poster \
  --scores runs/poster/artark/scores.jsonl \
  --weights 2:3:1.5:2:1.5 \
  --output runs/poster/artark/comparison.csv
```

For ADC, select `--track adc` and use the ADC manifest, briefs, rubric, prompts, and reference-image root. Keep output directories separate between tracks and models. New API calls may produce different images and judgments from the released annotations.

### Bundled adapters

Pass the CLI identifier to `generate_images.py --model`:

| Model | CLI identifier |
| --- | --- |
| Seedream 5.0 Pro | `doubao-seedream-5-0-pro-260628` |
| Nano Banana Pro | `nano-banana-pro` |
| GPT-Image-2.5 Flare | `gpt-image-2.5-flare` |
| GPT-Image-2 | `gpt-image-2` |
| GPT-Image-2.5 Sunburst | `gpt-image-2.5-sunburst` |
| ArtArk | `artark` |

See [the model configuration example](../configs/models.example.yaml) for provider mappings. Each script supports `--help`. Brief extraction and review scripts are included for creating additional tasks; they are not needed to evaluate the released benchmark.
