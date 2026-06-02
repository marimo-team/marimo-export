import { Buffer } from "node:buffer";
import fs from "node:fs/promises";
import path from "node:path";

import { createMarimoExportClient, parseExportSpec } from "@marimo-team/export-client";
import { arrowLoader } from "@marimo-team/export-loader-arrow";
import { readExport } from "@marimo-team/export-reader";

import { marimoNotebook, marimoServerToken, marimoServerUrl } from "@/lib/marimo-env";

export const marketWindows = [
  {
    start: "2026-01-01",
    end: "2026-05-01",
    title: "Year-to-date AI platform basket",
    note: "AAPL, MSFT, and GOOGL over the 2026 reporting window.",
  },
  {
    start: "2025-10-01",
    end: "2026-05-01",
    title: "Seven-month platform reset",
    note: "The same basket over a longer pre-earnings window.",
  },
] as const;

export const shouldBuildMarketWindowRoutes = (): boolean =>
  ["1", "true", "force"].includes((process.env.MARIMO_ARCHIVE_CAPTURE ?? "").toLowerCase());

export interface MarketWindowSummary {
  rows: number;
  columns: string[];
  symbols: string[];
  date_start: string;
  date_end: string;
  latest: Array<{
    symbol: string;
    first_close: number;
    close: number;
    close_change: number | null;
    window_return: number | null;
  }>;
}

export interface MarketWindowRow {
  Date: string;
  Symbol: string;
  Open: number;
  Close: number;
  "Close Change": number | null;
}

export interface MarketWindowPageData {
  start: string;
  end: string;
  title: string;
  note: string;
  scenario: string;
  manifestId: string;
  archiveBytes: number;
  notebookName: string | null;
  notebookSha: string | null;
  summary: MarketWindowSummary;
  sampleRows: MarketWindowRow[];
  arrowRows: MarketWindowRow[];
  chartDataUrl: string;
}

const ARCHIVE_CAPTURE_LOCK = path.join(process.cwd(), ".next", ".moexport-archive.lock");
const ARCHIVE_LOCK_STALE_MS = 180_000;

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
            pl.col("Close").first().alias("first_close"),
            pl.col("Close").last().alias("close"),
            pl.col("Close Change").last().alias("close_change"),
        ])
        .with_columns(
            ((pl.col("close") / pl.col("first_close")) - 1).alias("window_return")
        )
        .sort("Symbol")
        .rename({"Symbol": "symbol"})
        .to_dicts()
    ),
}`;

const sampleRowsSource = `(
    df.select(["Date", "Symbol", "Open", "Close", "Close Change"])
    .with_columns(pl.col("Date").dt.strftime("%Y-%m-%d").alias("Date"))
    .sort(["Date", "Symbol"])
    .tail(18)
    .to_dicts()
)`;

const arrowFrameSource = `(
    df.select(["Date", "Symbol", "Open", "Close", "Close Change"])
    .with_columns(pl.col("Date").dt.strftime("%Y-%m-%d").alias("Date"))
    .sort(["Date", "Symbol"])
    .tail(36)
)`;

const chartSource = `(
    alt.Chart(df)
    .mark_line()
    .encode(
        x=alt.X("Date:T", title=None),
        y=alt.Y("Close:Q", title="Close"),
        color=alt.Color("Symbol:N", title=None),
        tooltip=["Date:T", "Symbol:N", "Close:Q", "Close Change:Q"],
    )
    .properties(
        title="Close levels over selected market window",
        width=960,
        height=340,
    )
)`;

export const isMarketWindow = (start: string, end: string): boolean =>
  marketWindows.some((window) => window.start === start && window.end === end);

export const getMarketWindowPage = async (
  start: string,
  end: string,
): Promise<MarketWindowPageData> => {
  const window = marketWindows.find(
    (candidate) => candidate.start === start && candidate.end === end,
  );

  if (window === undefined) {
    throw new Error(`Unknown market window: ${start}/${end}`);
  }

  const scenario = scenarioId(start, end);
  const notebook = marimoNotebook();
  const serverToken = marimoServerToken();
  const sessionId = process.env.MARIMO_SESSION_ID;
  const sessionTarget = sessionId ? { sessionId } : { notebook };
  const client = createMarimoExportClient({
    server: marimoServerUrl(),
    ...(serverToken ? { serverToken } : {}),
  });
  const archive = await archiveSerially(() =>
    client.archive(buildMarketWindowSpec(start, end), {
      ...sessionTarget,
    }),
  );
  const arrow = arrowLoader({ useDate: true });
  const exp = await readExport({ bytes: archive.bytes });

  try {
    const summary = await exp
      .get({ scenario, value: "summary", format: "json" })
      .json<MarketWindowSummary>();
    const sampleRows = await exp
      .get({ scenario, value: "sample_rows", format: "json" })
      .json<MarketWindowRow[]>();
    const frame = await exp.get({ scenario, value: "frame", format: "arrow" }).load(arrow);
    const arrowRows = (await frame.rows()) as MarketWindowRow[];
    const chartPng = await exp.get({ scenario, value: "chart", format: "png" }).bytes();

    return {
      start,
      end,
      title: window.title,
      note: window.note,
      scenario,
      manifestId: exp.id,
      archiveBytes: archive.bytes.byteLength,
      notebookName: exp.notebook.name,
      notebookSha: exp.notebook.sourceSha256,
      summary,
      sampleRows,
      arrowRows: arrowRows.slice(-12),
      chartDataUrl: `data:image/png;base64,${Buffer.from(chartPng).toString("base64")}`,
    };
  } finally {
    exp.dispose();
  }
};

const buildMarketWindowSpec = (start: string, end: string) =>
  parseExportSpec({
    scenarios: [
      {
        id: scenarioId(start, end),
        state: {
          symbols: ["AAPL", "MSFT", "GOOGL"],
          interval: "1d",
          start,
          end,
          chart_width: 960,
        },
      },
    ],
    values: {
      summary: {
        source: { expr: summarySource },
        formats: [
          {
            json: {
              filename: "summary.json",
              format_id: "finance.market_window.summary.json.v1",
              metadata: {
                transport: "archive",
                kind: "market-window-summary",
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
              format_id: "finance.market_window.sample_rows.json.v1",
              metadata: {
                transport: "archive",
                kind: "market-window-sample-rows",
              },
            },
          },
        ],
      },
      frame: {
        source: { expr: arrowFrameSource },
        formats: ["arrow"],
      },
      chart: {
        source: { expr: chartSource },
        formats: [{ png: { scale: 2 } }],
      },
    },
  });

const archiveSerially = async <T>(capture: () => Promise<T>): Promise<T> => {
  const release = await acquireArchiveLock();

  try {
    return await capture();
  } finally {
    await release();
  }
};

const acquireArchiveLock = async (): Promise<() => Promise<void>> => {
  await fs.mkdir(path.dirname(ARCHIVE_CAPTURE_LOCK), { recursive: true });
  const deadline = Date.now() + 240_000;

  while (Date.now() < deadline) {
    try {
      await fs.mkdir(ARCHIVE_CAPTURE_LOCK);
      await fs.writeFile(
        path.join(ARCHIVE_CAPTURE_LOCK, "owner"),
        `${process.pid}\n${Date.now()}\n`,
      );
      return async () => {
        await fs.rm(ARCHIVE_CAPTURE_LOCK, { recursive: true, force: true });
      };
    } catch (error) {
      if (!isAlreadyExists(error)) {
        throw error;
      }

      await removeStaleArchiveLock();
      await sleep(500);
    }
  }

  throw new Error("Timed out waiting for marimo archive capture lock.");
};

const removeStaleArchiveLock = async (): Promise<void> => {
  try {
    const stat = await fs.stat(ARCHIVE_CAPTURE_LOCK);
    if (Date.now() - stat.mtimeMs > ARCHIVE_LOCK_STALE_MS) {
      await fs.rm(ARCHIVE_CAPTURE_LOCK, { recursive: true, force: true });
    }
  } catch (error) {
    if (!isNotFound(error)) {
      throw error;
    }
  }
};

const scenarioId = (start: string, end: string): string => `window-${start}-to-${end}`;

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

const isAlreadyExists = (error: unknown): boolean =>
  error instanceof Error && "code" in error && error.code === "EEXIST";

const isNotFound = (error: unknown): boolean =>
  error instanceof Error && "code" in error && error.code === "ENOENT";
