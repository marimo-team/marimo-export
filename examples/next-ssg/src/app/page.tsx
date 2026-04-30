import Link from "next/link";

import { getFinanceOverview } from "@/lib/local-export";
import { marketWindows, shouldBuildMarketWindowRoutes } from "@/lib/market-window-export";
import { financePairs } from "@/lib/pairs";

const HomePage = async () => {
  const overview = await getFinanceOverview();
  const buildArchiveRoutes = shouldBuildMarketWindowRoutes();

  return (
    <main className="shell">
      <header className="page-header">
        <div>
          <p className="eyebrow">marimo static export</p>
          <h1>Finance comparison pages</h1>
        </div>
        <div className="manifest-chip">
          <span>Bundle</span>
          <strong>{overview.manifestId}</strong>
        </div>
      </header>

      <section className="intro-grid" aria-label="Export source">
        <div>
          <p className="section-label">Notebook source</p>
          <p className="tight">
            {overview.notebookName ?? "unknown notebook"} ·{" "}
            {overview.notebookSha?.slice(0, 12) ?? "no source hash"}
          </p>
        </div>
        <div>
          <p className="section-label">Capture shape</p>
          <p className="tight">
            {overview.scenarios.length} precomputed scenarios · JSON summary · PNG raster ·
            Vega-Lite spec · named cell output · anywidget bundle
          </p>
        </div>
      </section>

      <section className="route-list" aria-label="Generated comparison routes">
        {financePairs.map((pair) => (
          <Link key={pair.slug} className="route-row" href={`/compare/${pair.slug}`}>
            <span>
              <strong>{pair.title}</strong>
              <small>{pair.note}</small>
            </span>
            <code>{pair.symbols.join(" / ")}</code>
          </Link>
        ))}
      </section>

      {buildArchiveRoutes ? (
        <section className="route-list" aria-label="Archive-backed routes">
          {marketWindows.map((window) => (
            <Link
              key={`${window.start}-${window.end}`}
              className="route-row"
              href={`/market-window/${window.start}/${window.end}`}
            >
              <span>
                <strong>{window.title}</strong>
                <small>{window.note}</small>
              </span>
              <code>
                {window.start} → {window.end}
              </code>
            </Link>
          ))}
        </section>
      ) : null}
    </main>
  );
};

export default HomePage;
