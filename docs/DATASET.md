# Dataset

DesignTaste contains 403 design tasks in two tracks. The Poster track contains 100 tasks based on award-winning works from *100 Beste Plakate 2025*. The ADC track contains 303 tasks spanning 20 categories from the ADC Annual Awards 2026. Of the initial 333 ADC tasks, 30 were excluded because one or more models refused image generation under their content moderation policies. Every retained task has scores for the award-winning work and all six models.

The release includes task metadata, briefs, scoring rubrics, SHA-256 identifiers, and score records. Each manifest row identifies a task and its reference filename. The brief describes the subject, purpose, and essential facts, while generally leaving composition, color, typography, and visual metaphor to the model. Use `brief_sha256` to verify that a brief has not changed. Original award-image directories and provider response archives are not included.

Raw scores are integers from 0 to 10 for each of five dimensions. Reported dimension scores and overall scores use a 0-100 scale. The default weights are `2:3:1.5:2:1.5`; see the [evaluation protocol](EVALUATION.md) for the dimension order and calculation. The source label `reference` identifies the award-winning work. Model labels are listed in [the model configuration](../configs/models.example.yaml).
