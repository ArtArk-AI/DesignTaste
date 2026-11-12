# Contributing

Keep changes reproducible and provider-agnostic. New model adapters should live under `src/designtaste/providers/` and read credentials only from environment variables or owner-only local configuration. Do not commit keys, proxy credentials, source-image corpora, provider response archives, or run directories.

New benchmark data must preserve stable `task_id` values, include SHA-256 checksums for briefs and images, and document its license in `data/LICENSE`. Changes to rubrics or weights should update the versioned rubric file and explain how old score records remain comparable.

Run the offline checks before opening a pull request:

```bash
PYTHONPATH=src pytest -q
PYTHONPATH=src python scripts/compare_scores.py --help
```
