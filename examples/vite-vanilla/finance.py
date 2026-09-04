# /// script
# dependencies = [
#     "altair==6.0.0",
#     "anywidget==0.11.0",
#     "marimo",
#     "polars==1.40.0",
#     "pyarrow==23.0.1",
#     "traitlets==5.14.3",
#     "yfinance==1.3.0",
# ]
# requires-python = ">=3.10"
# ///

import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def _():
    import altair as alt
    import marimo as mo
    import polars as pl
    import yfinance as yf

    from quote_detail import QuoteDetail

    return QuoteDetail, alt, mo, pl, yf


@app.cell
def _(mo):
    mo.md(r"""
    # Technology watchlist

    Compare large technology platforms and the AI buildout across one fixed
    month of market history. The notebook owns data retrieval, state
    selection, analysis, and the outputs consumed by the static application.
    """)
    return


@app.cell
def _(mo):
    symbols = ["AAPL", "CRWV", "MSFT", "GOOGL", "AMZN"]
    interval = "1d"
    start = "2025-04-01"
    end = "2025-05-02"
    chart_width = 980
    mo.md(f"""
    - **Analysis window:** `{start}` through `{end}` at `{interval}` resolution
    - **Universe:** {", ".join(symbols)}
    """)
    return chart_width, end, interval, start, symbols


@app.cell
def _(mo):
    mo.md(r"""
    ## Load and normalize

    Retrieve one fixed historical window, reshape the provider response into
    one row per trading session and symbol, then derive daily price changes.
    """)
    return


@app.cell(hide_code=True)
def _(end, interval, mo, pl, start, symbols, yf):
    history = pl.from_pandas(
        yf.Tickers(symbols)
        .history(
            interval=interval,
            start=start,
            end=end,
            progress=False,
        )
        .reset_index()
    )
    mo.vstack(
        [
            mo.md(f"Loaded **{history.height:,} provider rows** for **{len(symbols)} symbols**."),
            history,
        ]
    )
    return (history,)


@app.cell(hide_code=True)
def _(history, mo, pl):
    _wide_history = (
        history.rename({"('Date', '')": "Date"})
        .unpivot(index="Date")
        .with_columns(
            pl.col("variable").str.extract_groups(
                r"\('(?<Metric>[^']+)',\s*'(?<Symbol>[^']+)'\)"
            )
        )
        .unnest("variable")
        .pivot(
            index=["Date", "Symbol"],
            on="Metric",
            values="value",
        )
        .sort(["Symbol", "Date"])
    )
    df = _wide_history.with_columns(
        *(
            pl.col(_field)
            .pct_change()
            .over("Symbol")
            .alias(f"{_field} Change")
            for _field in ("Open", "High", "Low", "Close")
        )
    ).select(
        "Date",
        "Symbol",
        "Open",
        "Open Change",
        "High",
        "High Change",
        "Low",
        "Low Change",
        "Close",
        "Close Change",
    )
    _session_count = df.get_column("Date").n_unique()
    mo.vstack(
        [
            mo.md(
                f"Normalized **{df.height:,} price observations** across "
                f"**{_session_count} trading sessions**."
            ),
            df,
        ]
    )
    return (df,)


@app.cell
def _(mo):
    mo.md(r"""
    ## Choose a market view

    The watchlist control is the notebook input behind four saved company
    groups. A fifth exported state keeps the full watchlist and changes the
    sampling interval to weekly closes.
    """)
    return


@app.cell(hide_code=True)
def _(mo, symbols):
    symbols_selector = mo.ui.multiselect(
        options=symbols,
        label="Watchlist",
        value=["AAPL", "MSFT", "GOOGL"],
    )
    symbols_selector
    return (symbols_selector,)


@app.cell(hide_code=True)
def _(df, mo, pl, symbols_selector):
    selected_prices = (
        df.filter(pl.col("Symbol").is_in(symbols_selector.value))
        .sort(["Symbol", "Date"])
        .with_columns(
            (pl.col("Close") / pl.col("Close").first().over("Symbol") * 100).alias(
                "Indexed Close"
            )
        )
    )
    mo.md(
        f"Selected **{selected_prices.get_column('Symbol').n_unique()} companies** "
        f"and **{selected_prices.height:,} observations**."
    )
    return (selected_prices,)


@app.cell(hide_code=True)
def _(mo, pl, selected_prices):
    _latest_prices = selected_prices.group_by("Symbol", maintain_order=True).tail(1).select(
        "Symbol",
        pl.col("Date").dt.strftime("%b %d, %Y").alias("Session"),
        pl.col("Close").round(2),
        (pl.col("Close Change") * 100).round(1).alias("Day change (%)"),
    )
    _latest_rows = "\n".join(
        f"| {_row['Symbol']} | {_row['Session']} | ${_row['Close']:,.2f} | "
        f"{_row['Day change (%)']:+.1f}% |"
        for _row in _latest_prices.iter_rows(named=True)
    )
    mo.md(f"""
    | Symbol | Session | Close | Day change |
    | :-- | :-- | --: | --: |
    {_latest_rows}
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Compare relative performance

    Each series starts at 100, which makes period movement comparable across
    companies with different share prices.
    """)
    return


@app.cell(hide_code=True)
def _(alt, chart_width, selected_prices):
    _hover = alt.selection_point(fields=["Symbol"], on="pointerover")
    performance = (
        alt.Chart(selected_prices)
        .mark_line(strokeWidth=2.4)
        .encode(
            x=alt.X("Date:T", title=None),
            y=alt.Y(
                "Indexed Close:Q",
                title="Indexed close",
                scale=alt.Scale(zero=False),
            ),
            color=alt.Color(
                "Symbol:N",
                title=None,
                scale=alt.Scale(
                    range=["#0880ea", "#7c3aed", "#d97706", "#00876c", "#d84a4a"]
                ),
            ),
            opacity=alt.condition(_hover, alt.value(1), alt.value(0.38)),
            tooltip=[
                alt.Tooltip("Date:T", title="Session"),
                alt.Tooltip("Symbol:N", title="Ticker"),
                alt.Tooltip("Close:Q", title="Close", format=",.2f"),
                alt.Tooltip("Indexed Close:Q", title="Indexed", format=",.1f"),
            ],
        )
        .add_params(_hover)
        .properties(width=chart_width, height=340)
        .configure_axis(
            gridColor="#e2e8f0",
            labelColor="#64748b",
            titleColor="#64748b",
        )
        .configure_legend(
            labelColor="#0f172a",
            orient="top",
            symbolStrokeWidth=3,
        )
        .configure_view(stroke=None)
    )
    performance
    return (performance,)


@app.cell(hide_code=True)
def _(QuoteDetail, mo):
    quote_detail = mo.ui.anywidget(QuoteDetail())
    return (quote_detail,)


@app.cell(hide_code=True)
def _(mo, pl, quote_detail, selected_prices, symbols_selector):
    quote_detail.widget.rows = selected_prices.with_columns(
        pl.col("Date").cast(pl.String)
    ).to_dicts()
    quote_detail.widget.symbol = symbols_selector.value[0]
    market_explorer = quote_detail.widget
    mo.vstack([mo.md("## Inspect one quote"), quote_detail])
    return (market_explorer,)


@app.cell
def _(mo):
    mo.md(r"""
    ## Export contract

    `finance.export.yaml` prepares five named states. Every state publishes
    the same five outputs: `market_summary`, `price_history`,
    `performance_chart`, `performance_snapshot`, and `market_explorer`.
    Python, TypeScript, agents, and the dashboard can read the resulting
    notebook export after this notebook process stops.
    """)
    return


if __name__ == "__main__":
    app.run()
