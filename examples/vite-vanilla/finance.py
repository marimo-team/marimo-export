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
# requires-python = ">=3.11"
# ///

import marimo

__generated_with = "0.23.15"
app = marimo.App(width="medium")


@app.cell
def _():
    symbols = ["AAPL", "CRWV", "MSFT", "GOOGL", "AMZN"]
    interval = "1d"
    start = "2025-04-01"
    end = "2025-05-02"
    chart_width = 980
    return chart_width, end, interval, start, symbols


@app.cell(hide_code=True)
def _(end, interval, pl, start, symbols, yf):
    _history = pl.from_pandas(
        yf.Tickers(symbols)
        .history(
            interval=interval,
            start=start,
            end=end,
        )
        .reset_index()
    )
    _wide = (
        _history.rename({"('Date', '')": "Date"})
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
    df = _wide.with_columns(
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
    return (df,)


@app.cell(hide_code=True)
def _(mo, symbols):
    symbols_selector = mo.ui.multiselect(
        options=symbols,
        label="Watchlist",
        value=["AAPL", "MSFT", "GOOGL"],
    )
    return (symbols_selector,)


@app.cell(hide_code=True)
def _(df, pl, symbols_selector):
    selected_prices = (
        df.filter(pl.col("Symbol").is_in(symbols_selector.value))
        .sort(["Symbol", "Date"])
        .with_columns(
            (pl.col("Close") / pl.col("Close").first().over("Symbol") * 100).alias(
                "Indexed Close"
            )
        )
    )
    return (selected_prices,)


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
    return (performance,)


@app.cell(hide_code=True)
def _():
    import anywidget
    import traitlets

    class QuoteDetail(anywidget.AnyWidget):
        _esm = r"""
        const svgNS = "http://www.w3.org/2000/svg";

        function render({ model, el }) {
          const root = document.createElement("section");
          root.className = "quote-detail";
          const header = document.createElement("header");
          const heading = document.createElement("div");
          heading.innerHTML = "<span>Quote detail</span><strong>Daily close and range</strong>";
          const select = document.createElement("select");
          select.setAttribute("aria-label", "Ticker");
          header.append(heading, select);

          const stats = document.createElement("div");
          stats.className = "quote-stats";
          const svg = document.createElementNS(svgNS, "svg");
          svg.setAttribute("viewBox", "0 0 640 180");
          svg.setAttribute("role", "img");
          svg.setAttribute("aria-label", "Closing price history");
          root.append(header, stats, svg);
          el.append(root);

          const formatPrice = (value) =>
            new Intl.NumberFormat(undefined, {
              style: "currency",
              currency: "USD",
              maximumFractionDigits: 2,
            }).format(Number(value));
          const stat = (label, value) => {
            const item = document.createElement("div");
            const name = document.createElement("span");
            const result = document.createElement("strong");
            name.textContent = label;
            result.textContent = value;
            item.append(name, result);
            return item;
          };

          function draw() {
            const rows = model.get("rows") || [];
            const symbols = [...new Set(rows.map((row) => row.Symbol))];
            let symbol = model.get("symbol");
            if (!symbols.includes(symbol)) symbol = symbols[0] || "";
            select.replaceChildren(
              ...symbols.map((name) => {
                const option = document.createElement("option");
                option.value = name;
                option.textContent = name;
                option.selected = name === symbol;
                return option;
              }),
            );

            const series = rows
              .filter((row) => row.Symbol === symbol)
              .sort((left, right) => new Date(left.Date) - new Date(right.Date));
            const latest = series.at(-1);
            if (!latest) return;
            const first = series[0];
            const periodMove = Number(latest.Close) / Number(first.Close) - 1;
            stats.replaceChildren(
              stat("Close", formatPrice(latest.Close)),
              stat("Period", `${periodMove >= 0 ? "+" : ""}${(periodMove * 100).toFixed(1)}%`),
              stat("Day range", `${formatPrice(latest.Low)} – ${formatPrice(latest.High)}`),
            );

            const values = series.map((row) => Number(row.Close));
            const low = Math.min(...values);
            const high = Math.max(...values);
            const span = Math.max(high - low, 1);
            const x = (index) => 24 + (index / Math.max(series.length - 1, 1)) * 592;
            const y = (value) => 154 - ((value - low) / span) * 128;
            const path = document.createElementNS(svgNS, "path");
            path.setAttribute(
              "d",
              values
                .map((value, index) => `${index ? "L" : "M"}${x(index)} ${y(value)}`)
                .join(" "),
            );
            path.setAttribute("class", "quote-line");
            const guide = document.createElementNS(svgNS, "line");
            guide.setAttribute("x1", "24");
            guide.setAttribute("x2", "616");
            guide.setAttribute("y1", "154");
            guide.setAttribute("y2", "154");
            guide.setAttribute("class", "quote-guide");
            svg.replaceChildren(guide, path);
          }

          const changeSymbol = () => {
            model.set("symbol", select.value);
            model.save_changes();
          };
          select.addEventListener("change", changeSymbol);
          model.on("change:rows", draw);
          model.on("change:symbol", draw);
          draw();

          return () => {
            select.removeEventListener("change", changeSymbol);
            model.off("change:rows", draw);
            model.off("change:symbol", draw);
          };
        }

        export default { render };
        """
        _css = r"""
        .quote-detail {
          color: var(--foreground, #0f172a);
          display: grid;
          gap: 18px;
        }
        .quote-detail header {
          align-items: end;
          display: flex;
          justify-content: space-between;
        }
        .quote-detail header div {
          display: grid;
          gap: 2px;
        }
        .quote-detail header span,
        .quote-stats span {
          color: var(--muted-foreground, #64748b);
          font-size: 12px;
        }
        .quote-detail header strong {
          font-family: Lora, serif;
          font-size: 20px;
        }
        .quote-detail select {
          background: var(--surface, #fff);
          border: 1px solid var(--border, #e2e8f0);
          border-radius: 6px;
          color: inherit;
          font: inherit;
          min-height: 34px;
          padding: 4px 30px 4px 10px;
        }
        .quote-stats {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
        }
        .quote-stats div {
          border-left: 1px solid var(--border, #e2e8f0);
          display: grid;
          gap: 3px;
          padding-left: 14px;
        }
        .quote-stats div:first-child {
          border-left: 0;
          padding-left: 0;
        }
        .quote-stats strong {
          font-family: "Fira Mono", monospace;
          font-size: 14px;
        }
        .quote-detail svg {
          height: auto;
          width: 100%;
        }
        .quote-line {
          fill: none;
          stroke: #0880ea;
          stroke-linecap: round;
          stroke-linejoin: round;
          stroke-width: 3;
        }
        .quote-guide {
          stroke: var(--border, #e2e8f0);
          stroke-dasharray: 4 5;
        }
        @media (max-width: 520px) {
          .quote-stats {
            gap: 12px;
            grid-template-columns: 1fr;
          }
          .quote-stats div {
            border-left: 0;
            padding-left: 0;
          }
        }
        """

        rows = traitlets.List(traitlets.Dict(), default_value=[]).tag(sync=True)
        symbol = traitlets.Unicode("").tag(sync=True)

    return (QuoteDetail,)


@app.cell(hide_code=True)
def _(QuoteDetail, mo, pl, selected_prices, symbols_selector):
    quote_detail = mo.ui.anywidget(
        QuoteDetail(
            rows=selected_prices.with_columns(pl.col("Date").cast(pl.String)).to_dicts(),
            symbol=symbols_selector.value[0],
        )
    )
    return (quote_detail,)


@app.cell(hide_code=True)
def _():
    import altair as alt
    import marimo as mo
    import polars as pl
    import yfinance as yf

    return alt, mo, pl, yf


@app.cell(hide_code=True)
def _():
    from marimo_export.exporters.altair import png, vegalite
    from marimo_export.exporters.anywidget import bundle
    from marimo_export.exporters.parquet import table

    return bundle, png, table, vegalite


@app.cell(hide_code=True)
def _(bundle, quote_detail):
    market_explorer = bundle(quote_detail)
    return (market_explorer,)


@app.cell(hide_code=True)
def _(performance, vegalite):
    performance_chart = vegalite(performance)
    return (performance_chart,)


@app.cell(hide_code=True)
def _(performance, png):
    performance_snapshot = png(performance, scale=2)
    return (performance_snapshot,)


@app.cell(hide_code=True)
def _(selected_prices, table):
    price_history = table(
        selected_prices,
        compression="snappy",
        filename="price-history.parquet",
    )
    return (price_history,)


if __name__ == "__main__":
    app.run()
