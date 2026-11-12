# Evaluation protocol

Each model generates an image from the same brief without access to the award-winning work. The model judge scores each generated image and award-winning work independently, using the brief and the prompt and rubric for the selected track. The scoring input omits model names, filenames, and award status. The main judge is `gpt-6.1-sol`: 2,820 released annotations use this judge, while the award-winning work for ADC task `62340` has the judge label `codex_visual_review`.

## Dimensions and weights

The paper reports the same five dimension names for both tracks. Their detailed criteria differ to reflect poster design and the ADC design categories. The annotation files and rubric files retain the following identifiers:

| Reported dimension | Poster identifier | ADC identifier | Default weight |
| --- | --- | --- | ---: |
| Thematic Expression | `brief_communication` | `brief_fit` | 2 |
| Creative Originality | `conceptual_originality` | `concept_strength` | 3 |
| Visual Organization | `visual_organization` | `category_fit` | 1.5 |
| Aesthetic Expression | `aesthetic_expression` | `visual_craft` | 2 |
| Formal Completion | `formal_resolution` | `visible_communication` | 1.5 |

The judge returns five integer scores from 0 to 10, explanations for those scores, and an overall assessment. Reported dimension scores are the raw means multiplied by 10. The overall score is `10 * sum(weight * raw_score) / sum(weights)`, also on a 0-100 scale. Default weights are defined in [weights.yaml](../configs/weights.yaml).

## Comparing models

Images are paired with the award-winning work by `task_id`. Each task contributes equally to a model's mean. Comparisons report the mean score, the mean difference from award-winning works, and the numbers of tasks on which the model scores higher, equal, or lower. These counts compare independent image scores.

The released annotations are the scores used in the paper. To evaluate another model, keep the same task IDs, save one image per task as `<task_id>.png`, and run `scripts/score_images.py` with the matching manifest, briefs, rubric, and scoring prompt. Pass `--judge-model gpt-6.1-sol` to use the paper's judge. To compare existing or new annotations without further model calls, run `scripts/compare_scores.py`.
