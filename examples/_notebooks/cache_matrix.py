# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "marimo==0.23.14",
#     "marimo-export==0.0.0",
# ]
# [tool.uv.sources]
# marimo-export = { path = "../../packages/producer", editable = true }
# ///

import marimo

__generated_with = "0.23.14"
app = marimo.App(width="medium")


@app.cell
def _():
    import os
    from pathlib import Path

    import marimo as mo

    def record_execution(name: str) -> None:
        counter_dir = os.environ.get("MARIMO_EXPORT_COUNTER_DIR")
        if counter_dir is None:
            return
        path = Path(counter_dir) / f"{name}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        count = int(path.read_text()) if path.exists() else 0
        path.write_text(str(count + 1))

    return mo, record_execution


@app.cell
def _(mo):
    mo.md("""
    # Cache matrix

    The export plan runs three input scenarios across a definition and a UI
    control. Two scenarios share the same `scale`, so marimo can reuse the
    scale-dependent cell and its custom JSON projection.

    Set `MARIMO_EXPORT_COUNTER_DIR` to a temporary directory before starting
    the server to record authored-cell and projection executions separately.
    """)
    return


@app.cell
def _(mo):
    multiplier = mo.ui.slider(1, 5, value=2, label="Multiplier")
    multiplier  # noqa: B018
    return (multiplier,)


@app.cell
def _():
    scale = 2
    return (scale,)


@app.cell
def _(record_execution):
    def counted_json(value):
        import json

        from marimo_export import Projection

        record_execution("projection")
        payload = json.dumps(
            value,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return Projection(payload, format_id="json.v1", media_type="application/json")

    return (counted_json,)


@app.cell
def _(record_execution, scale):
    record_execution("projected")
    projected = {"value": scale * 10 + 1}
    return (projected,)


@app.cell
def _(multiplier, projected):
    reactive = {"value": projected["value"] * multiplier.value}
    return (reactive,)


@app.cell
def _(projected, reactive):
    chart = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.1.0.json",
        "data": {
            "values": [
                {"metric": "Projected", "value": projected["value"]},
                {"metric": "Reactive", "value": reactive["value"]},
            ]
        },
        "mark": {"type": "bar", "color": "#28634a"},
        "encoding": {
            "x": {"field": "metric", "type": "nominal", "title": None},
            "y": {"field": "value", "type": "quantitative", "title": "Value"},
        },
        "height": 220,
        "width": 420,
    }
    return (chart,)


@app.cell
def _(mo, reactive):
    reactive_markup = mo.md(f"**Reactive value:** {reactive['value']}")
    reactive_markup  # noqa: B018
    return (reactive_markup,)


@app.cell
def _(projected):
    projected  # noqa: B018
    return


if __name__ == "__main__":
    app.run()
