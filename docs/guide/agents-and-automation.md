---
title: Use notebook exports with agents
description: Give agents prepared notebook data, verify its export bytes, and retain exact state and representation evidence.
---

# Use notebook exports with agents

A notebook export gives an agent a finite, named data source. The agent can
verify the complete export, select an exported state, decode a supported output,
and retain the identities needed to reproduce its answer.

## Read a verified output

Build the [first notebook export](getting-started), then verify it in machine
mode:

```bash
uv run marimo-export verify dist/report --json
```

Example result:

```json
{
  "ok": true,
  "result": {
    "assets": 2,
    "bytes_verified": 923,
    "outputs": 4,
    "states": 2
  }
}
```

The exact byte count can change when the pinned marimo snapshot encoding
changes. The stable quickstart contract is two exported states, four
state-output pairs, two assets, and a positive verified-byte count.

Read the monthly summary and retain its evidence identity:

```python
from marimo_export import open_export

notebook_export = open_export("dist/report")
state = notebook_export.state("monthly")
output = state.output("summary")
report = state.output("report")

evidence = {
    "export_sha256": notebook_export.identity,
    "spec_sha256": notebook_export.spec_sha256,
    "state_sha256": state.fingerprint,
    "output": output.name,
    "codec": output.codec,
    "media_type": output.media_type,
    "python_type": output.descriptor.provenance.python_type,
}

print(output.json())
print(report.codec, len(report.asset_bytes()) > 0)
```

The selected representation determines what the agent can inspect. Pair a chart
or widget with JSON, Parquet, Arrow, or NumPy data when an answer depends on
exact values.

## Retain the evidence chain

Keep these facts with a data-driven answer or generated application:

- notebook filename and document SHA-256
- ExportSpec SHA-256
- notebook export identity from canonical `index.json`
- marimo and marimo-export producer versions
- state alias, complete inputs, and state fingerprint
- output name, codec, media type, and stored Python type. Exporter-backed
  outputs record `marimo_export.outputs.BlobAsset`
- asset SHA-256 when the output references an asset
- complete-export verification result

Verification proves that files match `index.json`. It does not authenticate the
publisher. Bind publisher identity through the storage, origin, signature, or
release mechanism used by the deployment.

## Ask an agent to create an export

An agent can use the CLI as a bounded workflow:

```bash
mkdir -p dist
uv run marimo-export inspect report.py --json
uv run marimo-export plan report.py --spec report.export.yaml --json
uv run marimo-export build report.py \
  --spec report.export.yaml \
  --output dist/report \
  --jsonl
uv run marimo-export verify dist/report --json
```

File inspection and preparation execute notebook code with the producer
process's environment, working directory, file access, credentials, packages,
and network access. Review the notebook and selected outputs before allowing an
agent to run them.

The plan reports complete state vectors, reusable state fingerprints, and
missing work. [Reuse earlier results](../concepts/preparation-and-reuse)
defines when an exact match can return before notebook startup and how external
data freshness enters producer identity and marimo caching.

## Ask an agent to create a browser application

The repository includes a [notebook-to-static-app
workflow](https://github.com/marimo-team/marimo-export/blob/main/skills/notebook-to-static-app/SKILL.md).
It guides an agent through notebook inspection, ExportSpec authoring,
preparation, application implementation, and browser validation.

Require the resulting application to:

- exercise every exported state
- keep the last committed view during rapid changes
- dispose replaced mounts
- surface recoverable errors
- load notebook results from the deployed export origin
- open no Python kernel or WebSocket for exported state changes

Use [Build a browser application](browser-applications) for the consumer
lifecycle and [Troubleshooting](troubleshooting) for evidence to collect when
an agent workflow fails.
