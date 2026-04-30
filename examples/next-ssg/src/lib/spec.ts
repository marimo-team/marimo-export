import { financePairs, type FinancePair } from "@/lib/pairs";
import { marimoNotebook } from "@/lib/marimo-env";

export const jsonExporter = `
import json


def _default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def export(value, ctx, **options):
    filename = options.get("filename", "value.json")
    format_id = options.get("format", "json.v1")
    blob = ctx.write_blob(
        filename,
        json.dumps(value, allow_nan=False, default=_default, indent=2).encode("utf-8"),
        media_type="application/json",
    )
    return {
        "format": format_id,
        "media_type": "application/json",
        "data": {
            "type": "bundle",
            "files": {"data": blob},
            "entry": "data",
        },
        "metadata": options.get("metadata", {}),
    }
`;

export const htmlExporter = `
def export(value, ctx, **options):
    html = value.text if hasattr(value, "text") else str(value)
    blob = ctx.write_blob(
        options.get("filename", "value.html"),
        html.encode("utf-8"),
        media_type="text/html",
    )
    return {
        "format": options.get("format", "marimo.cell_output.html.v1"),
        "media_type": "text/html",
        "data": {
            "type": "bundle",
            "files": {"html": blob},
            "entry": "html",
        },
        "metadata": options.get("metadata", {}),
    }
`;

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

const changeDescSource = `mox.runtime().cell("change_desc").output`;

export const buildFinanceSpec = (pairs: readonly FinancePair[] = financePairs) => ({
  notebook: marimoNotebook(),
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
      source: summarySource,
      formats: {
        json: {
          export: {
            type: "code",
            code: jsonExporter,
          },
          options: {
            filename: "summary.json",
            format: "finance.summary.json.v1",
            metadata: {
              kind: "finance-summary",
            },
          },
        },
      },
    },
    sample_rows: {
      source: sampleRowsSource,
      formats: {
        json: {
          export: {
            type: "code",
            code: jsonExporter,
          },
          options: {
            filename: "sample-rows.json",
            format: "finance.sample_rows.json.v1",
            metadata: {
              kind: "finance-sample-rows",
            },
          },
        },
      },
    },
    change_desc: {
      source: changeDescSource,
      formats: {
        html: {
          export: {
            type: "code",
            code: htmlExporter,
          },
          options: {
            filename: "change-desc.html",
            format: "marimo.cell_output.html.v1",
            metadata: {
              kind: "marimo-cell-output",
              cell: "change_desc",
            },
          },
        },
      },
    },
    chart: {
      source: "symbols_chart",
      formats: {
        vegalite: {
          export: {
            type: "ref",
            ref: "moexport.exporters.altair:vegalite",
          },
        },
        png: {
          export: {
            type: "ref",
            ref: "moexport.exporters.altair:png",
          },
          options: {
            scale: 2,
          },
        },
      },
    },
    ohlc_dashboard: {
      source: "widget",
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
});

export type FinanceSpec = ReturnType<typeof buildFinanceSpec>;
