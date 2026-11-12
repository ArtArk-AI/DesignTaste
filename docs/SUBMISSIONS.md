# Model submissions

A model submission is a directory containing one image for every selected `task_id`, named `<task_id>.png`. Keep the brief files and model name unchanged. Record the model version, generation settings, and failures in your own run metadata; the benchmark score file stores the image hash, rubric scores, rationales, and overall assessment. The comparison script computes aggregate scores from those records.

The benchmark does not require a provider-specific API. The bundled adapters are convenience integrations for the six models used in the release. A local or hosted model can be evaluated by producing the same submission directory and using the scoring script.
