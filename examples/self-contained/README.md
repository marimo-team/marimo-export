# Self-contained Markdown exports

This example writes a linear notebook snapshot to a PR-reviewable folder:

- `output.md` contains every notebook cell in order, with authored Python source
  and display outputs below each cell label.
- Inline HTML display outputs are converted to Markdown with `markdownify` so
  rendered prose and tables remain native Markdown where possible.
- `media/` contains large or scripted display outputs referenced by the
  Markdown.

The generator keeps the Markdown-specific policy inside this example. It uses
the core package only for capture/query primitives:

1. Build a spec whose value source is `{snapshot: true}`.
2. Export it through `moexport.exporters.notebook:linear`.
3. Read the finished bundle with `open_export` from `moexport.query`.
4. Materialize the linear notebook format as Markdown and static media from
   this example's `notebook_markdown.py`.

Regenerate the finance export from the repository root:

```bash
uv run --with-requirements examples/self-contained/requirements.txt \
  python examples/self-contained/generate.py \
  notebooks/finance.py \
  examples/self-contained/finance \
  --state-json examples/self-contained/finance-state.json \
  --scenario-id finance-review \
  --title "Finance notebook static review"
```

`finance-state.json` contains one `state` object. Plain keys override notebook
definitions, and dotted keys patch object attributes.

Preview it as HTML:

```bash
quarto preview examples/self-contained/finance/output.md --port 4321 --no-browser
```
