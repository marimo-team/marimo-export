# Notebook fixtures

Source notebooks and export specs used by the package tests and examples.

- `finance.py`: main fixture for dataframe, Vega-Lite, PNG, HTML, and
  AnyWidget exports.
- `queueing_lab.py`: deterministic scenario-matrix fixture for frameworkless
  examples.
- `agentic_playground.py`: small fixture for inline custom exporters.
- `export-specs/yaml`: canonical export specs.
- `export-specs/json`: deterministic JSON copies generated from YAML.

Regenerate JSON specs from the repository root:

```bash
uv run --no-project --script scripts/sync_specs.py
```

The sync script validates that YAML values are JSON-compatible and writes
matching filenames under `export-specs/json`.
