# Linear program publication

This example publishes three objective vectors and their CVXPY solutions from a
public marimo notebook.

Run the complete build from this directory:

```bash
uv run marimo-export build 02_linear_program.py \
  --spec linear-program.export.yaml \
  --output publication

uv run marimo-export verify publication
```

The local uv project selects Python 3.13, installs the notebook dependencies
from `uv.lock`, and installs `marimo-export` from `../../packages/python`.

`02_linear_program.py` is an exact snapshot of the
[`marimo-team/learn` notebook](https://github.com/marimo-team/learn/blob/477e2cbf7c31fc05dcf307b1e9c92c36514a32f3/optimization/02_linear_program.py)
at commit `477e2cbf7c31fc05dcf307b1e9c92c36514a32f3`. Its SHA-256 digest is
`a14b48a6fcb2d472dd0ed9cf299ae7676bae7104e3f038915121e13e1b25288d`.
