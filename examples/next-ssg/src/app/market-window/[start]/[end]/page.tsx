import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import {
  getMarketWindowPage,
  isMarketWindow,
  marketWindows,
  shouldBuildMarketWindowRoutes,
  type MarketWindowRow,
} from "@/lib/market-window-export";

export const dynamicParams = false;

interface MarketWindowPageProps {
  params: Promise<{
    start: string;
    end: string;
  }>;
}

export const generateStaticParams = () =>
  marketWindows.map(({ start, end }) => ({
    end,
    start,
  }));

export const generateMetadata = async ({ params }: MarketWindowPageProps): Promise<Metadata> => {
  const { start, end } = await params;
  if (!isMarketWindow(start, end)) {
    return {};
  }

  return {
    title: `${start} to ${end} | marimo archive market window`,
    description: "Static market-window report rendered from an in-memory marimo export archive.",
  };
};

const MarketWindowPage = async ({ params }: MarketWindowPageProps) => {
  const { start, end } = await params;

  if (!isMarketWindow(start, end)) {
    notFound();
  }

  if (!shouldBuildMarketWindowRoutes()) {
    return <MarketWindowPlaceholder start={start} end={end} />;
  }

  const page = await getMarketWindowPage(start, end);

  return (
    <main className="shell">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link href="/">Reports</Link>
        <span>
          {start} to {end}
        </span>
      </nav>

      <header className="page-header compare-header">
        <div>
          <p className="eyebrow">archive-backed SSG · {page.scenario}</p>
          <h1>{page.title}</h1>
          <p className="lede">
            {page.note} Close levels, recent sessions, and window returns are pinned to this
            captured market range.
          </p>
        </div>
        <dl className="stat-strip" aria-label="Market window summary">
          <div>
            <dt>Requested</dt>
            <dd>
              {start} → {end}
            </dd>
          </div>
          <div>
            <dt>Rows</dt>
            <dd>{page.summary.rows.toLocaleString()}</dd>
          </div>
          <div>
            <dt>Archive</dt>
            <dd>{formatBytes(page.archiveBytes)}</dd>
          </div>
        </dl>
      </header>

      <section className="summary-grid market-summary-grid" aria-label="Window return summary">
        {page.summary.latest.map((row) => (
          <article key={row.symbol} className="metric-cell">
            <span>{row.symbol}</span>
            <strong>{formatMoney(row.close)}</strong>
            <small>
              {formatSignedPercent(row.window_return)} window ·{" "}
              {formatSignedPercent(row.close_change)} latest session
            </small>
          </article>
        ))}
      </section>

      <section className="cell" aria-label="Market window chart">
        <div className="cell-heading">
          <div>
            <p className="eyebrow">image.png.v1 · archive bytes</p>
            <h2>Close levels</h2>
          </div>
          <span>{page.summary.symbols.join(" / ")}</span>
        </div>
        <img
          className="chart-image"
          src={page.chartDataUrl}
          alt={`Close level chart for ${page.summary.symbols.join(", ")} from ${start} to ${end}`}
        />
      </section>

      <section className="cell" aria-label="Archive provenance">
        <div className="cell-heading">
          <div>
            <p className="eyebrow">bundle provenance</p>
            <h2>Captured archive</h2>
          </div>
          <span>{page.manifestId}</span>
        </div>
        <div className="archive-facts">
          <div>
            <span>Data range</span>
            <strong>
              {page.summary.date_start} → {page.summary.date_end}
            </strong>
          </div>
          <div>
            <span>Notebook</span>
            <strong>{page.notebookName ?? "unknown notebook"}</strong>
          </div>
          <div>
            <span>Source sha</span>
            <strong>{page.notebookSha?.slice(0, 12) ?? "no source hash"}</strong>
          </div>
          <div>
            <span>Formats</span>
            <strong>JSON summary · Arrow frame · PNG chart</strong>
          </div>
        </div>
      </section>

      <section className="output-grid" aria-label="Archive dataframe outputs">
        <article className="cell">
          <div className="cell-heading">
            <div>
              <p className="eyebrow">finance.market_window.sample_rows.json.v1</p>
              <h2>JSON sample rows</h2>
            </div>
            <span>custom exporter</span>
          </div>
          <DataTable rows={page.sampleRows.slice(-9)} />
        </article>

        <article className="cell">
          <div className="cell-heading">
            <div>
              <p className="eyebrow">dataframe.arrow.v1</p>
              <h2>Arrow sample rows</h2>
            </div>
            <span>flechette loader</span>
          </div>
          <DataTable rows={page.arrowRows.slice(-9)} />
        </article>
      </section>
    </main>
  );
};

const MarketWindowPlaceholder = ({ start, end }: { start: string; end: string }) => {
  const window = marketWindows.find(
    (candidate) => candidate.start === start && candidate.end === end,
  );

  if (window === undefined) {
    notFound();
  }

  return (
    <main className="shell">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link href="/">Reports</Link>
        <span>
          {start} to {end}
        </span>
      </nav>
      <header className="page-header compare-header">
        <div>
          <p className="eyebrow">archive-backed SSG</p>
          <h1>{window.title}</h1>
          <p className="lede">{window.note}</p>
        </div>
      </header>
    </main>
  );
};

const DataTable = ({ rows }: { rows: MarketWindowRow[] }) => (
  <div className="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Date</th>
          <th>Symbol</th>
          <th>Open</th>
          <th>Close</th>
          <th>Close Change</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={`${row.Date}-${row.Symbol}`}>
            <td>{formatDate(row.Date)}</td>
            <td>{row.Symbol}</td>
            <td>{formatMoney(row.Open)}</td>
            <td>{formatMoney(row.Close)}</td>
            <td>{formatSignedPercent(row["Close Change"])}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const formatBytes = (bytes: number): string =>
  new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 1,
    style: "unit",
    unit: "kilobyte",
  }).format(bytes / 1024);

const formatMoney = (value: number): string =>
  new Intl.NumberFormat("en-US", {
    currency: "USD",
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
    style: "currency",
  }).format(value);

const formatDate = (value: string): string => value.slice(0, 10);

const formatSignedPercent = (value: number | null): string =>
  value === null
    ? "n/a"
    : new Intl.NumberFormat("en-US", {
        maximumFractionDigits: 2,
        minimumFractionDigits: 2,
        signDisplay: "exceptZero",
        style: "percent",
      }).format(value);

export default MarketWindowPage;
