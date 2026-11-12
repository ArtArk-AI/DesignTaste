# Release checklist

Before publishing a fork or a tagged release:

- Verify the authors and repository URL in `CITATION.cff`.
- Add the publication identifier to `CITATION.cff` when available.
- Confirm that no populated environment file, source-image directory, provider response, or run directory is tracked.
- Record any separately hosted image archive and its license in `data/LICENSE`.
- Run `pytest -q` and the offline comparison command in `docs/REPRODUCING_RESULTS.md`.
