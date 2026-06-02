import type { ExportSpec } from "@marimo-team/export-client";

import { financePairs, type FinancePair } from "@/lib/pairs";

const summarySource = `{
    "rows": df.height,
    "columns": df.columns,
    "symbols": sorted(df["Symbol"].unique().to_list()),
    "date_start": df["Date"].min().strftime("%Y-%m-%d"),
    "date_end": df["Date"].max().strftime("%Y-%m-%d"),
    "latest": (
        df.sort("Date")
        .group_by("Symbol")
        .agg([
            pl.col("Close").last().alias("close"),
            pl.col("Close Change").last().alias("close_change"),
        ])
        .sort("Symbol")
        .rename({"Symbol": "symbol"})
        .to_dicts()
    ),
}`;

const sampleRowsSource = `(
    df.select(["Date", "Symbol", "Open", "Close", "Close Change"])
    .with_columns(pl.col("Date").dt.strftime("%Y-%m-%d").alias("Date"))
    .sort(["Date", "Symbol"])
    .tail(12)
    .to_dicts()
)`;

export const buildFinanceSpec = (pairs: readonly FinancePair[] = financePairs) =>
  ({
    scenarios: pairs.map((pair) => ({
      id: pair.slug,
      state: {
        symbols: pair.symbols,
        interval: "1d",
        start: "2025-04-01",
        end: "2026-05-01",
        chart_width: 960,
      },
    })),
    values: {
      summary: {
        source: { expr: summarySource },
        formats: [
          {
            json: {
              filename: "summary.json",
              format_id: "finance.summary.json.v1",
              metadata: {
                kind: "finance-summary",
              },
            },
          },
        ],
      },
      sample_rows: {
        source: { expr: sampleRowsSource },
        formats: [
          {
            json: {
              filename: "sample-rows.json",
              format_id: "finance.sample_rows.json.v1",
              metadata: {
                kind: "finance-sample-rows",
              },
            },
          },
        ],
      },
      change_desc: {
        source: { cell: "change_desc" },
        formats: [
          {
            html: {
              filename: "change-desc.html",
              format_id: "marimo.cell_output.html.v1",
              metadata: {
                kind: "marimo-cell-output",
                cell: "change_desc",
              },
            },
          },
        ],
      },
      chart: {
        source: { def: "symbols_chart" },
        formats: ["vegalite", { png: { scale: 2 } }],
      },
      ohlc_dashboard: {
        source: { def: "widget" },
        formats: {
          bundle: {
            export: {
              type: "ref",
              ref: "moexport.exporters.anywidget:bundle",
            },
          },
        },
      },
    },
  }) satisfies ExportSpec;

export type FinanceSpec = ReturnType<typeof buildFinanceSpec>;
