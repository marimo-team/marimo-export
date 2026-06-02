import { readExport, type Export, type ExportEntry } from "@marimo-team/export-reader";
import {
  anywidgetLoader,
  createWidgetStore,
  type LoadedAnyWidget,
} from "@marimo-team/export-loader-anywidget";
import { arrowLoader, type ArrowTable } from "@marimo-team/export-loader-arrow";
import { parquetLoader, type ParquetFile } from "@marimo-team/export-loader-parquet";
import {
  vegaliteLoader,
  type VegaLiteChart,
  type VegaLiteRenderResult,
  type VegaLiteSpec,
} from "@marimo-team/export-loader-vegalite";

import "@/style.css";

interface SummaryPayload {
  rows: number;
  columns: string[];
  symbols: string[];
  date_start: string;
  date_end: string;
}

interface PriceRow {
  Date: string;
  Symbol: string;
  Open: number;
  "Open Change": number | null;
  High: number;
  "High Change": number | null;
  Low: number;
  "Low Change": number | null;
  Close: number;
  "Close Change": number | null;
}

interface OhlcWidgetState {
  title: string;
  rows: PriceRow[];
  metric: "Open" | "High" | "Low" | "Close";
  mode: "absolute" | "change";
  selected_symbols: string[];
}

interface SymbolsSelectorPayload {
  label: string;
  options: string[];
  value: string[];
}

interface DemoState {
  ready: boolean;
  scenario?: string;
  manifestId?: string;
  summary?: SummaryPayload;
  symbolsSelector?: SymbolsSelectorPayload;
  changeDescHtml?: string;
  arrowRows?: PriceRow[];
  parquetRows?: PriceRow[];
  widgetState?: Readonly<OhlcWidgetState>;
  chartPngUrl?: string;
  vegaliteSpec?: VegaLiteSpec;
  errors: string[];
}

interface LoadedScenarioData {
  summary: SummaryPayload;
  symbolsSelector: SymbolsSelectorPayload;
  arrow: ArrowTable;
  arrowRows: PriceRow[];
  parquet: ParquetFile;
  parquetRows: PriceRow[];
  parquetMeta: { rows: number; columns: string[] };
  vegalite: VegaLiteChart;
  vegaliteSpec: VegaLiteSpec;
  png: ExportEntry;
  changeDesc: ExportEntry;
  changeDescHtml: string;
  widget: LoadedAnyWidget<OhlcWidgetState>;
}

declare global {
  interface Window {
    __STATIC_EXPORT_DEMO__?: DemoState;
  }
}

const EXPORT_ROOT = "/export/";
const STATUS_IDS = [
  "widget-status",
  "selector-status",
  "desc-status",
  "vegalite-status",
  "png-status",
  "arrow-status",
  "parquet-status",
  "matrix-status",
] as const;

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) {
  throw new Error("Missing #app root.");
}

window.__STATIC_EXPORT_DEMO__ = {
  ready: false,
  errors: [],
};

app.innerHTML = `
  <main class="app-shell">
    <header class="workspace-header">
      <div class="workspace-heading">
        <div>
          <p class="eyebrow">Static finance export</p>
          <h1>Market monitor</h1>
          <p class="header-copy">Precomputed states from <code>finance.py</code>.</p>
        </div>
        <div class="header-meta" aria-live="polite">
          <span id="transition-status" class="status" data-status="loading">loading</span>
        </div>
      </div>
    </header>
    <nav id="scenario-tabs" class="scenario-tabs" aria-label="Scenarios"></nav>

    <dl class="market-strip" aria-label="Market summary">
      <div>
        <dt>Active state</dt>
        <dd id="scenario-id">loading</dd>
      </div>
      <div>
        <dt>Selection</dt>
        <dd id="coverage">loading</dd>
      </div>
      <div>
        <dt>Window</dt>
        <dd id="date-window">loading</dd>
      </div>
      <div>
        <dt>Rows</dt>
        <dd id="row-count">loading</dd>
      </div>
    </dl>

    <section class="state-summary" aria-label="Selected portfolio state">
      <div>
        <p id="selector-meta" class="meta-line">loading</p>
        <div id="selector-states" class="selector-states"></div>
      </div>
      <div id="change-desc" class="notebook-note"></div>
      <span id="selector-status" class="status utility-status">loading</span>
      <span id="desc-status" class="status utility-status">loading</span>
    </section>

    <section id="dashboard-stage" class="dashboard-stage" aria-busy="true">
      <article class="cell widget-cell">
        <header class="cell-header">
          <div>
            <p class="eyebrow">Dashboard</p>
            <h2>Cross-sectional OHLC review</h2>
          </div>
          <span id="widget-status" class="status">loading</span>
        </header>
        <div id="widget-root" class="widget-root"></div>
        <p id="widget-state" class="meta-line">loading</p>
      </article>

      <section class="chart-stack" aria-label="Chart outputs">
        <article class="cell">
          <header class="cell-header">
            <div>
              <p class="eyebrow">Chart</p>
              <h2>Close-price trajectory</h2>
            </div>
            <span id="vegalite-status" class="status">loading</span>
          </header>
          <div id="interactive-chart" class="vega-host">
            <div id="interactive-chart-render" class="vega-render"></div>
          </div>
          <p id="vegalite-summary" class="meta-line">loading</p>
        </article>

        <article class="cell">
          <header class="cell-header">
            <div>
              <p class="eyebrow">Snapshot</p>
              <h2>No-grid PNG snapshot</h2>
            </div>
            <span id="png-status" class="status">loading</span>
          </header>
          <img id="chart-image" class="chart-image" alt="Close change chart exported as PNG" />
          <p id="chart-meta" class="meta-line">loading</p>
        </article>
      </section>

      <details class="cell data-details" open>
        <summary>
          <div>
            <p class="eyebrow">Data</p>
            <h2>Decoded price tables</h2>
          </div>
          <span>Arrow and Parquet</span>
        </summary>
        <section class="output-grid" aria-label="Dataframe outputs">
          <article class="data-panel">
            <header class="panel-header">
              <div>
                <h3>Arrow sample</h3>
                <p id="arrow-meta" class="meta-line">loading</p>
              </div>
              <span id="arrow-status" class="status">loading</span>
            </header>
            <div id="arrow-table" class="table-frame"></div>
          </article>

          <article class="data-panel">
            <header class="panel-header">
              <div>
                <h3>Parquet cross-check</h3>
                <p id="parquet-meta" class="meta-line">loading</p>
              </div>
              <span id="parquet-status" class="status">loading</span>
            </header>
            <div id="parquet-table" class="table-frame"></div>
          </article>
        </section>
      </details>

      <details class="cell diagnostics-cell">
        <summary>
          <div>
            <p class="eyebrow">Export diagnostics</p>
            <h2>Format contract</h2>
          </div>
          <span id="matrix-status" class="status">loading</span>
        </summary>
        <dl class="format-strip" aria-label="Export formats">
          <div>
            <dt>Bundle</dt>
            <dd id="manifest-id">loading</dd>
          </div>
          <div>
            <dt>Formats</dt>
            <dd id="format-list">loading</dd>
          </div>
        </dl>
        <pre id="trace" class="code-output">loading</pre>
      </details>
    </section>
  </main>
`;

const byId = <T extends HTMLElement>(id: string): T => {
  const node = document.querySelector<T>(`#${id}`);
  if (!node) {
    throw new Error(`Missing #${id}`);
  }
  return node;
};

const setText = (id: string, value: string): void => {
  byId(id).textContent = value;
};

const setStatus = (id: string, value: "ready" | "error" | "loading"): void => {
  const node = byId(id);
  node.textContent = value;
  node.dataset.status = value;
};

const setAllStatuses = (status: "ready" | "error" | "loading"): void => {
  for (const id of STATUS_IDS) {
    setStatus(id, status);
  }
};

const setTransitionStatus = (value: string, status: "ready" | "error" | "loading"): void => {
  const node = byId("transition-status");
  node.textContent = value;
  node.dataset.status = status;
};

let activeUnmount: (() => Promise<void>) | null = null;
let activeWidget: LoadedAnyWidget<OhlcWidgetState> | null = null;
let activeChart: VegaLiteRenderResult | null = null;
let activeLoad = 0;

async function main(): Promise<void> {
  try {
    const exp = await readExport({ root: EXPORT_ROOT });

    renderScenarioTabs(exp);
    setText("manifest-id", exp.id);
    setText("format-list", formatSummary(exp));

    await loadScenario(exp, firstScenario(exp));
  } catch (error) {
    fail(error);
  }
}

function renderScenarioTabs(exp: Export): void {
  const target = byId("scenario-tabs");
  target.replaceChildren();

  for (const scenario of exp.scenarios()) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.scenario = scenario;
    button.textContent = labelScenarioFromManifest(exp, scenario);
    target.append(button);
  }

  target.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>(
      "button[data-scenario]",
    );
    if (!button) {
      return;
    }
    void loadScenario(exp, button.dataset.scenario ?? firstScenario(exp));
  });
}

function firstScenario(exp: Export): string {
  const scenario =
    exp.scenarios().find((candidate) => candidate === "selector_crwv_msft") ?? exp.scenarios()[0];
  if (!scenario) {
    throw new Error("Export manifest does not contain any scenarios.");
  }
  return scenario;
}

async function loadScenario(exp: Export, scenario: string): Promise<void> {
  const loadId = ++activeLoad;
  try {
    beginScenarioTransition(exp, scenario);
    const data = await loadScenarioData(exp, scenario);
    if (loadId !== activeLoad) {
      data.widget.dispose();
      return;
    }

    await commitScenario(exp, scenario, data);
    completeScenarioTransition();
  } catch (error) {
    if (loadId !== activeLoad) {
      return;
    }
    fail(error);
  }
}

async function loadScenarioData(exp: Export, scenario: string): Promise<LoadedScenarioData> {
  const arrowData = arrowLoader({ useDate: true });
  const parquetData = parquetLoader();
  const vegaliteChart = vegaliteLoader({ actions: true });
  const ohlcWidget = anywidgetLoader<OhlcWidgetState>();
  const summaryPromise = exp
    .get({ scenario, value: "summary", format: "json" })
    .json<SummaryPayload>();
  const symbolsSelectorPromise = exp
    .get({ scenario, value: "symbols_selector", format: "json" })
    .json<SymbolsSelectorPayload>();
  const arrowPromise = exp.get({ scenario, value: "prices", format: "arrow" }).load(arrowData);
  const parquetPromise = exp
    .get({ scenario, value: "prices", format: "parquet" })
    .load(parquetData);
  const vegalitePromise = exp
    .get({ scenario, value: "comparison_chart", format: "vegalite" })
    .load(vegaliteChart);
  const png = exp.get({ scenario, value: "comparison_chart", format: "png_nogrid" });
  const changeDesc = exp.get({ scenario, value: "change_desc", format: "html" });
  const widgetPromise = exp
    .get({ scenario, value: "ohlc_dashboard", format: "bundle" })
    .load(ohlcWidget);

  const [summary, symbolsSelector, arrow, parquet, vegalite, widget] = await Promise.all([
    summaryPromise,
    symbolsSelectorPromise,
    arrowPromise,
    parquetPromise,
    vegalitePromise,
    widgetPromise,
  ]);
  const [arrowRows, parquetRows, parquetMeta, vegaliteSpec, changeDescHtml] = await Promise.all([
    arrow.rows({ useDate: true }).then(normalizeRows),
    parquet.readRows().then(normalizeRows),
    parquet.readMetadata(),
    vegalite.spec(),
    changeDesc.text(),
  ]);

  return {
    summary,
    symbolsSelector,
    arrow,
    arrowRows,
    parquet,
    parquetRows,
    parquetMeta,
    vegalite,
    vegaliteSpec,
    png,
    changeDesc,
    changeDescHtml,
    widget,
  };
}

async function commitScenario(
  exp: Export,
  scenario: string,
  data: LoadedScenarioData,
): Promise<void> {
  const {
    summary,
    symbolsSelector,
    arrow,
    arrowRows,
    parquet,
    parquetRows,
    parquetMeta,
    vegalite,
    vegaliteSpec,
    png,
    changeDesc,
    changeDescHtml,
    widget,
  } = data;

  await clearWidget();
  clearInteractiveChart();

  renderTable("arrow-table", arrowRows.slice(0, 8));
  renderTable("parquet-table", parquetRows.slice(0, 8));
  byId("change-desc").innerHTML = changeDescHtml;
  renderSelectorStates(exp, scenario, symbolsSelector);
  setText("scenario-id", labelScenarioFromManifest(exp, scenario));
  setText(
    "coverage",
    `${symbolsSelector.value.join(", ")} selected · ${summary.symbols.length} in universe`,
  );
  setText("date-window", `${summary.date_start} to ${summary.date_end}`);
  setText("row-count", summary.rows.toLocaleString());
  setText(
    "arrow-meta",
    `${arrowRows.length.toLocaleString()} rows · Arrow stream · ${arrow.blob.size.toLocaleString()} bytes`,
  );
  setText(
    "parquet-meta",
    `${parquetMeta.rows.toLocaleString()} rows · ${parquetMeta.columns.length} columns · ${parquet.blob.size.toLocaleString()} bytes`,
  );

  activeChart = await vegalite.render(byId("interactive-chart-render"));
  setText("vegalite-summary", compactChartSummary(vegaliteSpec));

  const chartImage = byId<HTMLImageElement>("chart-image");
  chartImage.src = png.url();
  setText(
    "chart-meta",
    `${png.entry().ref.size.toLocaleString()} bytes · ${summary.symbols.length} symbols · ${png.mediaType}`,
  );

  const mount = await widget.mount(byId("widget-root"));
  activeWidget = widget;
  activeUnmount = mount.unmount;
  const store = createWidgetStore<OhlcWidgetState>(mount.widget);
  store.select(
    (state) => `${state.metric}|${state.mode}|${state.selected_symbols.join(",")}`,
    (_key, _previous, state) => {
      setText(
        "widget-state",
        `${state.metric} · ${state.mode === "change" ? "relative move" : "spot level"} · ${state.selected_symbols.join(", ")}`,
      );
      window.__STATIC_EXPORT_DEMO__ = {
        ready: true,
        scenario,
        manifestId: exp.id,
        summary,
        symbolsSelector,
        changeDescHtml,
        arrowRows,
        parquetRows,
        widgetState: state,
        chartPngUrl: png.url(),
        vegaliteSpec,
        errors: [],
      };
    },
  );

  byId("trace").textContent = JSON.stringify(
    {
      manifest: exp.id,
      sourceSpec: exp.sourceSpecSha256,
      scenario,
      state: exp.scenario(scenario).state,
      checks: {
        arrowRows: arrowRows.length,
        parquetRows: parquetRows.length,
        summaryRows: summary.rows,
        changeDescBytes: changeDesc.entry().ref.size,
        widgetRows: widget.initialState.rows.length,
        selectorValue: symbolsSelector.value,
        symbolUniverse: symbolsSelector.options,
      },
      loaders: {
        anywidget: widget.formatId,
        cellOutput: changeDesc.formatId,
        arrow: "dataframe.arrow.v1",
        parquet: "dataframe.parquet.v1",
        vegalite: vegalite.formatId,
      },
      rawFormats: {
        summary: "summary.json.v1",
        customPng: png.formatId,
      },
    },
    null,
    2,
  );

  setAllStatuses("ready");
}

async function clearWidget(): Promise<void> {
  if (activeUnmount) {
    await activeUnmount();
    activeUnmount = null;
  }
  if (activeWidget) {
    activeWidget.dispose();
    activeWidget = null;
  }
  byId("widget-root").replaceChildren();
}

function clearInteractiveChart(): void {
  activeChart?.finalize();
  activeChart = null;
  byId("interactive-chart-render").replaceChildren();
}

function renderTable(targetId: string, rows: PriceRow[]): void {
  const target = byId(targetId);
  target.innerHTML = `
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
        ${rows
          .map(
            (row) => `
              <tr>
                <td>${row.Date}</td>
                <td>${row.Symbol}</td>
                <td>${formatNumber(row.Open)}</td>
                <td>${formatNumber(row.Close)}</td>
                <td>${formatChange(row["Close Change"])}</td>
              </tr>
            `,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

function normalizeRows(rows: unknown[]): PriceRow[] {
  return rows.map((raw) => {
    const row = raw as Record<string, unknown>;
    return {
      Date: normalizeDate(row.Date),
      Symbol: String(row.Symbol),
      Open: number(row.Open),
      "Open Change": optionalNumber(row["Open Change"]),
      High: number(row.High),
      "High Change": optionalNumber(row["High Change"]),
      Low: number(row.Low),
      "Low Change": optionalNumber(row["Low Change"]),
      Close: number(row.Close),
      "Close Change": optionalNumber(row["Close Change"]),
    };
  });
}

function markScenario(scenario: string): void {
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-scenario]")) {
    button.dataset.active = String(button.dataset.scenario === scenario);
  }
}

function renderSelectorStates(
  exp: Export,
  activeScenario: string,
  selector: SymbolsSelectorPayload,
): void {
  const target = byId("selector-states");
  target.replaceChildren();

  for (const symbol of selector.options) {
    const chip = document.createElement("span");
    chip.textContent = symbol;
    chip.dataset.active = String(selector.value.includes(symbol));
    target.append(chip);
  }

  setText(
    "selector-meta",
    `${labelScenarioFromManifest(exp, activeScenario)} from ${selector.options.length} available names`,
  );
}

function beginScenarioTransition(exp: Export, scenario: string): void {
  const root = byId("dashboard-stage");
  root.dataset.loading = "true";
  root.setAttribute("aria-busy", "true");
  markScenario(scenario);
  setScenarioButtonsDisabled(true);
  setAllStatuses("loading");
  setTransitionStatus(`loading ${labelScenarioFromManifest(exp, scenario)}`, "loading");
}

function completeScenarioTransition(): void {
  const root = byId("dashboard-stage");
  root.dataset.loading = "false";
  root.setAttribute("aria-busy", "false");
  setScenarioButtonsDisabled(false);
  setTransitionStatus("ready", "ready");
}

function setScenarioButtonsDisabled(disabled: boolean): void {
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-scenario]")) {
    button.disabled = disabled;
  }
}

function formatSummary(exp: Export): string {
  return exp
    .values()
    .map((value) => `${value}: ${exp.formats(value).join(", ")}`)
    .join(" · ");
}

function labelScenario(value: string): string {
  return value.replaceAll("_", " ");
}

function labelScenarioFromManifest(exp: Export, scenario: string): string {
  const value = selectorValue(exp.scenario(scenario).state);
  return value.length ? value.join(" + ") : labelScenario(scenario);
}

function selectorValue(state: Record<string, unknown>): string[] {
  const value = state["symbols_selector.value"];
  return Array.isArray(value) ? value.map(String) : [];
}

function compactChartSummary(spec: VegaLiteSpec): string {
  const summary = summarizeSpec(spec);
  const encoding = Array.isArray(summary.encoding) ? summary.encoding.join(", ") : "unknown";
  return `${String(summary.mark)} mark · ${encoding} encodings · width ${String(summary.width)}`;
}

function summarizeSpec(spec: VegaLiteSpec): Record<string, unknown> {
  const encoding = isRecord(spec.encoding) ? Object.keys(spec.encoding) : [];
  const datasets = isRecord(spec.datasets) ? Object.keys(spec.datasets) : [];
  const data = isRecord(spec.data) ? spec.data : null;
  return {
    schema: spec.$schema,
    mark: spec.mark,
    width: spec.width,
    height: spec.height,
    data,
    datasets,
    encoding,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function number(value: unknown): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    throw new Error(`Expected finite number, got ${String(value)}`);
  }
  return parsed;
}

function optionalNumber(value: unknown): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  return number(value);
}

function normalizeDate(value: unknown): string {
  if (value instanceof Date) {
    return value.toISOString().slice(0, 10);
  }
  return String(value).slice(0, 10);
}

function formatNumber(value: number): string {
  return value.toLocaleString(undefined, {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  });
}

function formatChange(value: number | null): string {
  if (value === null) {
    return "n/a";
  }
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;
}

function fail(error: unknown): never {
  const message = error instanceof Error ? error.message : String(error);
  setScenarioButtonsDisabled(false);
  const stage = document.querySelector<HTMLElement>("#dashboard-stage");
  if (stage) {
    stage.dataset.loading = "false";
    stage.setAttribute("aria-busy", "false");
  }
  window.__STATIC_EXPORT_DEMO__ = {
    ready: false,
    errors: [message],
  };
  setAllStatuses("error");
  setTransitionStatus("error", "error");
  byId("trace").textContent = message;
  throw error;
}

await main();
