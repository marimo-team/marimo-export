import marimo

__generated_with = "0.24.0"
app = marimo.App()


@app.cell
def _():
    import anywidget
    import marimo as mo
    import polars as pl
    import traitlets

    return anywidget, mo, pl, traitlets


@app.cell
def _(anywidget, traitlets):
    class MarketDetail(anywidget.AnyWidget):
        _esm = """
        function render({ model, el }) {
          const output = document.createElement("output");
          const draw = () => {
            output.textContent = `${model.get("symbol")}: ${model.get("rows").length} rows`;
          };
          model.on("change", draw);
          draw();
          el.append(output);
          return () => model.off("change", draw);
        }
        export default { render };
        """

        rows = traitlets.List(traitlets.Dict(), default_value=[]).tag(sync=True)
        symbol = traitlets.Unicode("").tag(sync=True)

    return (MarketDetail,)


@app.cell
def _(mo):
    symbols = ["ALPHA", "BETA", "GAMMA"]
    symbols_selector = mo.ui.multiselect(
        options=symbols,
        value=["ALPHA", "BETA"],
        label="Market series",
    )
    return (symbols_selector,)


@app.cell
def _(symbols_selector):
    history = [
        {"date": "2026-01-02", "symbol": "ALPHA", "close": 100.0},
        {"date": "2026-01-03", "symbol": "ALPHA", "close": 102.0},
        {"date": "2026-01-04", "symbol": "ALPHA", "close": 104.0},
        {"date": "2026-01-02", "symbol": "BETA", "close": 80.0},
        {"date": "2026-01-03", "symbol": "BETA", "close": 84.0},
        {"date": "2026-01-04", "symbol": "BETA", "close": 82.0},
        {"date": "2026-01-02", "symbol": "GAMMA", "close": 120.0},
        {"date": "2026-01-03", "symbol": "GAMMA", "close": 118.0},
        {"date": "2026-01-04", "symbol": "GAMMA", "close": 126.0},
    ]
    selected_rows = [row for row in history if row["symbol"] in symbols_selector.value]
    return (selected_rows,)


@app.cell
def _(selected_rows):
    market_summary = {
        "schema": "marimo-export.test-market.v1",
        "symbols": sorted({row["symbol"] for row in selected_rows}),
        "observations": len(selected_rows),
    }
    performance = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "data": {"values": selected_rows},
        "mark": "line",
        "encoding": {
            "x": {"field": "date", "type": "temporal"},
            "y": {"field": "close", "type": "quantitative"},
            "color": {"field": "symbol", "type": "nominal"},
        },
    }
    return market_summary, performance


@app.cell
def _(pl, selected_rows):
    price_history = pl.DataFrame(selected_rows)
    return (price_history,)


@app.cell
def _(MarketDetail, mo, selected_rows, symbols_selector):
    detail = mo.ui.anywidget(MarketDetail())
    detail.widget.rows = selected_rows
    detail.widget.symbol = symbols_selector.value[0]
    market_explorer = detail.widget
    return (market_explorer,)


if __name__ == "__main__":
    app.run()
