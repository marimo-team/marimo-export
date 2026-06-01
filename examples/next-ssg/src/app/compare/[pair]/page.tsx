import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { OhlcWidgetPanel } from "@/components/ohlc-widget-panel";
import { VegaLiteChart } from "@/components/vega-lite-chart";
import { getFinancePairPage } from "@/lib/local-export";
import { financePairs, getFinancePair } from "@/lib/pairs";

export const dynamicParams = false;

interface ComparePageProps {
  params: Promise<{
    pair: string;
  }>;
}

export const generateStaticParams = () => financePairs.map((pair) => ({ pair: pair.slug }));

export const generateMetadata = async ({ params }: ComparePageProps): Promise<Metadata> => {
  const { pair: slug } = await params;
  const pair = getFinancePair(slug);

  if (!pair) {
    return {};
  }

  return {
    title: `${pair.title} | marimo export SSG`,
    description: pair.note,
  };
};

const ComparePage = async ({ params }: ComparePageProps) => {
  const { pair: slug } = await params;
  const pair = getFinancePair(slug);

  if (!pair) {
    notFound();
  }

  const page = await getFinancePairPage(pair.slug);

  return (
    <main className="shell">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link href="/">Comparisons</Link>
        <span>{pair.symbols.join(" / ")}</span>
      </nav>

      <header className="page-header compare-header">
        <div>
          <p className="eyebrow">precomputed scenario · {page.scenario}</p>
          <h1>{pair.title}</h1>
          <p className="lede">{pair.note}</p>
        </div>
        <dl className="stat-strip" aria-label="Scenario summary">
          <div>
            <dt>Rows</dt>
            <dd>{page.summary.rows.toLocaleString()}</dd>
          </div>
          <div>
            <dt>Window</dt>
            <dd>
              {page.summary.date_start} → {page.summary.date_end}
            </dd>
          </div>
          <div>
            <dt>Notebook</dt>
            <dd>{page.notebookName ?? "unknown"}</dd>
          </div>
        </dl>
      </header>

      <section className="summary-grid" aria-label="Latest close values">
        {page.summary.latest.map((row) => (
          <article key={row.symbol} className="metric-cell">
            <span>{row.symbol}</span>
            <strong>{formatMoney(row.close)}</strong>
            <small>{formatPercent(row.close_change)} latest daily change</small>
          </article>
        ))}
      </section>

      <section className="cell" aria-label="Exported notebook markdown cell">
        <div className="cell-heading">
          <div>
            <p className="eyebrow">marimo.cell_output.html.v1</p>
            <h2>Notebook-authored runtime</h2>
          </div>
          <span>{'{ cell: "change_desc", output: "scenario" }'}</span>
        </div>
        <div
          className="markdown-output"
          dangerouslySetInnerHTML={{ __html: page.changeDescHtml }}
        />
      </section>

      <section className="cell" aria-label="Hydrated anywidget">
        <div className="cell-heading">
          <div>
            <p className="eyebrow">anywidget.bundle.v1</p>
            <h2>OHLC dashboard widget</h2>
          </div>
          <span>React controls and widget controls share one model</span>
        </div>
        <OhlcWidgetPanel scenario={page.scenario} />
      </section>

      <section className="stack" aria-label="Chart exports">
        <article className="cell">
          <div className="cell-heading">
            <div>
              <p className="eyebrow">image.png.v1</p>
              <h2>Raster export</h2>
            </div>
            <a href={page.pngUrl}>Open PNG</a>
          </div>
          <img
            className="chart-image"
            src={page.pngUrl}
            alt={`Rasterized close-change chart for ${pair.title}`}
          />
        </article>

        <article className="cell">
          <div className="cell-heading">
            <div>
              <p className="eyebrow">client island</p>
              <h2>Interactive Vega-Lite export</h2>
            </div>
            <span>Loaded from the same static bundle</span>
          </div>
          <VegaLiteChart scenario={page.scenario} />
        </article>
      </section>

      <section className="cell" aria-label="Sample rows">
        <div className="cell-heading">
          <div>
            <p className="eyebrow">custom code exporter · finance.sample_rows.json.v1</p>
            <h2>Latest materialized rows</h2>
          </div>
          <span>{page.manifestId}</span>
        </div>
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
              {page.sampleRows.map((row) => (
                <tr key={`${row.Date}-${row.Symbol}`}>
                  <td>{formatDate(row.Date)}</td>
                  <td>{row.Symbol}</td>
                  <td>{formatMoney(row.Open)}</td>
                  <td>{formatMoney(row.Close)}</td>
                  <td>{formatPercent(row["Close Change"])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
};

const formatMoney = (value: number): string =>
  new Intl.NumberFormat("en-US", {
    currency: "USD",
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
    style: "currency",
  }).format(value);

const formatDate = (value: string): string => value.slice(0, 10);

const formatPercent = (value: number | null): string =>
  value === null
    ? "n/a"
    : new Intl.NumberFormat("en-US", {
        maximumFractionDigits: 2,
        minimumFractionDigits: 2,
        style: "percent",
      }).format(value);

export default ComparePage;
