# Publish marimo results for a Python-free client

marimo-export executes declared notebook states through marimo, reads the
native cache returns, and writes a static publication:

```text
ExportSpec
  -> complete state vectors
  -> normal marimo execution
  -> scalar, NPY, Arrow, or BlobAsset receipts
  -> canonical index.json plus content-addressed assets
  -> explicit browser OutputLoaders
```

Use `build` when marimo-export should own notebook startup and shutdown:

```bash
marimo-export build notebook.py \
  --spec notebook.export.yaml \
  --output dist/notebook
```

Use `capture` when a live kernel already contains the environment and expensive
results:

```bash
marimo-export capture http://127.0.0.1:2718 \
  --spec notebook.export.yaml \
  --output dist/notebook
```

Both paths invoke the same matrix engine. The publication is a finite relation
from complete input vectors to typed outputs. Client interaction means choosing
an available vector and running browser behavior over its verified assets.

Continue with [Getting started](getting-started.md).
