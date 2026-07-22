# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "altair==6.0.0",
#     "marimo==0.23.14",
#     "marimo-export[dataframe,png]==0.0.0",
# ]
# [tool.uv.sources]
# marimo-export = { path = "../../packages/producer", editable = true }
# ///

import marimo

__generated_with = "0.23.14"
app = marimo.App(width="medium")


@app.cell
def _():
    import altair as alt
    import marimo as mo

    return alt, mo


@app.cell
def _(mo):
    mo.md("""
    # Offline market report

    This notebook uses a small embedded price series so every scenario can be
    published reproducibly. Change the symbols or lookback window to exercise
    marimo's reactive graph before exporting the same objects for frontend use.
    """)
    return


@app.cell
def _():
    dates = (
        "2026-06-01",
        "2026-06-02",
        "2026-06-03",
        "2026-06-04",
        "2026-06-05",
        "2026-06-08",
    )
    close_prices = {
        "AAPL": (201.4, 203.1, 202.7, 205.8, 207.2, 206.6),
        "MSFT": (472.2, 475.8, 478.1, 476.9, 481.3, 484.0),
        "NVDA": (142.8, 144.5, 143.9, 147.6, 149.1, 151.4),
    }
    market_rows = [
        {
            "date": _date,
            "symbol": _symbol,
            "open": round(_close - 0.8, 2),
            "high": round(_close + 1.4, 2),
            "low": round(_close - 1.7, 2),
            "close": _close,
        }
        for _symbol, _closes in close_prices.items()
        for _date, _close in zip(dates, _closes, strict=True)
    ]
    return (market_rows,)


@app.cell
def _():
    window_days = 5
    return (window_days,)


@app.cell
def _():
    chart_width = 640
    return (chart_width,)


@app.cell
def _(market_rows, mo):
    available_symbols = sorted({row["symbol"] for row in market_rows})
    symbol_picker = mo.ui.multiselect(
        options=available_symbols,
        value=available_symbols[:2],
        label="Symbols",
    )
    symbol_picker  # noqa: B018
    return available_symbols, symbol_picker


@app.cell
def _(market_rows, symbol_picker, window_days):
    _dates = sorted({row["date"] for row in market_rows})
    _visible_dates = set(_dates[-window_days:])
    _visible_symbols = set(symbol_picker.value)
    visible_rows = [
        row
        for row in market_rows
        if row["date"] in _visible_dates and row["symbol"] in _visible_symbols
    ]
    return (visible_rows,)


@app.cell
def _(symbol_picker, visible_rows, window_days):
    _latest = {
        _symbol: next(row["close"] for row in reversed(visible_rows) if row["symbol"] == _symbol)
        for _symbol in symbol_picker.value
    }
    summary = {
        "symbols": list(symbol_picker.value),
        "window_days": window_days,
        "rows": len(visible_rows),
        "latest_close": _latest,
    }
    return (summary,)


@app.cell
def _(alt, chart_width, visible_rows):
    price_chart = (
        alt.Chart(alt.Data(values=visible_rows))
        .mark_line(point=True)
        .encode(
            x=alt.X("date:T", title="Trading day"),
            y=alt.Y("close:Q", title="Close"),
            color=alt.Color("symbol:N", title="Symbol"),
            tooltip=["date:T", "symbol:N", "open:Q", "high:Q", "low:Q", "close:Q"],
        )
        .properties(width=chart_width, height=300, title="Selected closing prices")
    )
    return (price_chart,)


@app.cell
def _(mo, symbol_picker, visible_rows):
    _start = min(row["date"] for row in visible_rows)
    _end = max(row["date"] for row in visible_rows)
    market_note = mo.md(
        f"Showing **{', '.join(symbol_picker.value)}** from **{_start}** through **{_end}**."
    )
    market_note  # noqa: B018
    return (market_note,)


@app.cell
def _(mo, price_chart, symbol_picker):
    mo.vstack([symbol_picker, price_chart])
    return


if __name__ == "__main__":
    app.run()
