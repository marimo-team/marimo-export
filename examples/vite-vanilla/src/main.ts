import { imageLoader, openExport } from "@marimo-team/marimo-export";
import type { MountedView, NotebookExport, ExportState } from "@marimo-team/marimo-export";
import { anyWidgetLoader } from "@marimo-team/marimo-export/loader/anywidget";
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

import { marketSummaryLoader } from "./market-summary";
import type { MarketSummary } from "./market-summary";
import "./style.css";

interface ViewCopy {
  readonly button: string;
  readonly cadence: string;
  readonly description: string;
  readonly title: string;
}

interface PriceRow {
  readonly "Close Change": number;
  readonly Close: number;
  readonly Date: unknown;
  readonly High: number;
  readonly Low: number;
  readonly Symbol: string;
}

const views: Readonly<Record<string, ViewCopy>> = {
  baseline: {
    button: "Leaders",
    cadence: "Daily close",
    description: "Apple, Microsoft, and Alphabet",
    title: "Market leaders",
  },
  cloud_platforms: {
    button: "Cloud",
    cadence: "Daily close",
    description: "Microsoft, Alphabet, and Amazon",
    title: "Cloud platforms",
  },
  ai_buildout: {
    button: "AI buildout",
    cadence: "Daily close",
    description: "CoreWeave alongside Microsoft and Alphabet",
    title: "AI infrastructure",
  },
  full_watchlist: {
    button: "All names",
    cadence: "Daily close",
    description: "The complete five-company watchlist",
    title: "Full watchlist",
  },
  weekly_view: {
    button: "Weekly",
    cadence: "Weekly close",
    description: "The full watchlist with weekly movement",
    title: "Weekly pulse",
  },
};

const companyNames: Readonly<Record<string, string>> = {
  AAPL: "Apple",
  AMZN: "Amazon",
  CRWV: "CoreWeave",
  GOOGL: "Alphabet",
  MSFT: "Microsoft",
};

const dashboard = required<HTMLElement>("#dashboard");
const viewButtons = required<HTMLElement>("#view-buttons");
const status = required<HTMLElement>("#status");
const errorPanel = required<HTMLElement>("#error-panel");
const errorMessage = required<HTMLElement>("#error-message");
const viewWindow = required<HTMLElement>("#view-window");
const viewTitle = required<HTMLElement>("#view-title");
const viewDescription = required<HTMLElement>("#view-description");
const leaderSymbol = required<HTMLElement>("#leader-symbol");
const leaderReturn = required<HTMLElement>("#leader-return");
const averageReturn = required<HTMLElement>("#average-return");
const sessionCount = required<HTMLElement>("#session-count");
const latestDate = required<HTMLElement>("#latest-date");
const latestSummary = required<HTMLElement>("#latest-summary");
const latestRows = required<HTMLElement>("#latest-rows");
const changePeriod = required<HTMLElement>("#change-period");
const chartHost = required<HTMLElement>("#performance-chart");
const explorerHost = required<HTMLElement>("#market-explorer");
const snapshotHost = required<HTMLElement>("#performance-snapshot");

let notebookExport: NotebookExport | undefined;
let mounted: MountedView[] = [];
let active: AbortController | undefined;
let revision = 0;
let transition = Promise.resolve();

void start();

async function start(): Promise<void> {
  try {
    const root = new URLSearchParams(location.search).get("export") ?? "./export/";
    const opened = await openExport(root);
    await opened.verify();
    notebookExport = opened;
    renderViewButtons(opened);
    requestView(viewFromHash(opened));
  } catch (error) {
    showError(error);
  }
}

function renderViewButtons(value: NotebookExport): void {
  const buttons = Object.entries(views)
    .filter(([name]) => hasState(value, name))
    .map(([name, copy]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.view = name;
      button.textContent = copy.button;
      button.addEventListener("click", () => requestView(name));
      return button;
    });
  viewButtons.replaceChildren(...buttons);
}

function requestView(name: string): void {
  const state = notebookExport?.state(name);
  const copy = views[name];
  if (state === undefined || copy === undefined) return;

  const nextRevision = ++revision;
  active?.abort();
  const controller = new AbortController();
  active = controller;
  setBusy(name, true);
  history.replaceState(null, "", `#${name}`);
  transition = transition.then(
    () => renderView(state, copy, nextRevision, controller),
    () => renderView(state, copy, nextRevision, controller),
  );
}

async function renderView(
  state: ExportState,
  copy: ViewCopy,
  nextRevision: number,
  controller: AbortController,
): Promise<void> {
  const { signal } = controller;
  try {
    const [rowsValue, summary, chart, image, widget] = await Promise.all([
      state.output("price_history").load(parquetRowsLoader(), { signal }),
      state.output("market_summary").load(marketSummaryLoader(), { signal }),
      state
        .output("performance_chart")
        .load(vegaLiteLoader({ actions: false, renderer: "svg" }), { signal }),
      state.output("performance_snapshot").load(imageLoader(), { signal }),
      state.output("market_explorer").load(anyWidgetLoader(), { signal }),
    ]);
    signal.throwIfAborted();
    if (nextRevision !== revision) return;

    const rows = parseRows(rowsValue);
    await disposeMounted(mounted.splice(0));
    signal.throwIfAborted();
    if (nextRevision !== revision) return;
    chartHost.replaceChildren();
    explorerHost.replaceChildren();
    snapshotHost.replaceChildren();

    const nextMounted: MountedView[] = [];
    try {
      nextMounted.push(await chart.mount(chartHost, { signal }));
      nextMounted.push(await widget.mount(explorerHost, { signal }));
      nextMounted.push(await image.mount(snapshotHost, { signal }));
      await imageReady(required<HTMLImageElement>("#performance-snapshot img"), signal);
    } catch (error) {
      await disposeMounted(nextMounted);
      throw error;
    }
    if (nextRevision !== revision) {
      await disposeMounted(nextMounted);
      return;
    }

    mounted = nextMounted;
    renderMarketView(rows, summary, copy);
    setBusy(state.name, false);
    errorPanel.hidden = true;
  } catch (error) {
    if (!signal.aborted && nextRevision === revision) showError(error);
  } finally {
    if (active === controller) active = undefined;
  }
}

function renderMarketView(rows: readonly PriceRow[], summary: MarketSummary, copy: ViewCopy): void {
  const series = groupBySymbol(rows);
  const returns = summary.periodReturns.map(({ return: periodReturn, symbol }) => {
    const latest = series.get(symbol)?.at(-1);
    if (latest === undefined) throw new Error(`${symbol} has no exported prices.`);
    return { latest, return: periodReturn, symbol };
  });

  viewWindow.textContent = `${formatDate(summary.firstSession, "short")} to ${formatDate(
    summary.lastSession,
    "long",
  )} · ${copy.cadence}`;
  viewTitle.textContent = copy.title;
  viewDescription.textContent = copy.description;
  leaderSymbol.textContent = summary.leader.symbol;
  leaderReturn.textContent = formatPercent(summary.leader.return);
  averageReturn.textContent = formatPercent(summary.averageReturn);
  sessionCount.textContent = String(summary.sessionCount);
  latestDate.textContent = formatDate(summary.lastSession, "short");
  latestSummary.textContent = `${summary.companyCount} companies · ${summary.observationCount} observations`;
  changePeriod.textContent = copy.cadence === "Weekly close" ? "Week" : "Day";
  latestRows.replaceChildren(
    ...returns
      .sort((left, right) => right.return - left.return)
      .map(({ latest, return: periodReturn }) => marketRow(latest, periodReturn, summary.currency)),
  );
}

function marketRow(row: PriceRow, periodReturn: number, currency: string): HTMLTableRowElement {
  const result = document.createElement("tr");
  result.dataset.symbol = row.Symbol;
  const company = document.createElement("td");
  const name = document.createElement("strong");
  const ticker = document.createElement("span");
  name.textContent = companyNames[row.Symbol] ?? row.Symbol;
  ticker.textContent = row.Symbol;
  company.append(name, ticker);
  result.append(
    company,
    cell(formatCurrency(row.Close, currency), "numeric"),
    cell(formatPercent(row["Close Change"]), percentClass(row["Close Change"])),
    cell(formatPercent(periodReturn), percentClass(periodReturn)),
    cell(
      `${formatCurrency(row.Low, currency)} to ${formatCurrency(row.High, currency)}`,
      "numeric",
    ),
  );
  return result;
}

function parseRows(values: readonly Record<string, unknown>[]): readonly PriceRow[] {
  const rows = values.map((value) => ({
    "Close Change": numberValue(value["Close Change"]),
    Close: requiredNumber(value.Close, "Close"),
    Date: value.Date,
    High: requiredNumber(value.High, "High"),
    Low: requiredNumber(value.Low, "Low"),
    Symbol: String(value.Symbol),
  }));
  if (rows.length === 0) throw new Error("The selected market view has no prices.");
  return rows;
}

function groupBySymbol(rows: readonly PriceRow[]): ReadonlyMap<string, readonly PriceRow[]> {
  const grouped = new Map<string, PriceRow[]>();
  for (const row of rows) {
    const values = grouped.get(row.Symbol) ?? [];
    values.push(row);
    grouped.set(row.Symbol, values);
  }
  for (const values of grouped.values()) {
    values.sort((left, right) => epochMilliseconds(left.Date) - epochMilliseconds(right.Date));
  }
  if (grouped.size === 0) throw new Error("The selected market view has no companies.");
  return grouped;
}

function setBusy(name: string, busy: boolean): void {
  dashboard.setAttribute("aria-busy", String(busy));
  dashboard.dataset.loading = String(busy);
  for (const button of viewButtons.querySelectorAll<HTMLButtonElement>("button")) {
    const selected = button.dataset.view === name;
    button.dataset.active = String(selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  status.textContent = busy
    ? `Loading ${views[name]?.title ?? "market view"}`
    : `${views[name]!.title} ready`;
}

function showError(error: unknown): void {
  console.error(error);
  dashboard.setAttribute("aria-busy", "false");
  dashboard.dataset.loading = "false";
  errorPanel.hidden = false;
  errorMessage.textContent = "Reload the page to try again.";
  status.textContent = "Market data could not be opened";
}

function viewFromHash(value: NotebookExport): string {
  const name = location.hash.slice(1);
  return name in views && hasState(value, name) ? name : "baseline";
}

function hasState(value: NotebookExport, name: string): boolean {
  try {
    value.state(name);
    return true;
  } catch {
    return false;
  }
}

function cell(value: string, className: string): HTMLTableCellElement {
  const result = document.createElement("td");
  result.className = className;
  result.textContent = value;
  return result;
}

function percentClass(value: number): string {
  return value >= 0 ? "numeric positive" : "numeric negative";
}

function formatPercent(value: number): string {
  if (!Number.isFinite(value)) return "n/a";
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}

function formatCurrency(value: number, currency: string): string {
  return new Intl.NumberFormat("en-US", {
    currency,
    maximumFractionDigits: 2,
    style: "currency",
  }).format(value);
}

function formatDate(value: number, length: "short" | "long"): string {
  return new Intl.DateTimeFormat("en-US", {
    day: "numeric",
    month: "short",
    year: length === "long" ? "numeric" : undefined,
  }).format(value);
}

function epochMilliseconds(value: unknown): number {
  if (value instanceof Date) return value.valueOf();
  if (typeof value === "string") return Date.parse(value);
  if (typeof value !== "number" && typeof value !== "bigint") return Number.NaN;
  let result = Number(value);
  const magnitude = Math.abs(result);
  if (magnitude < 100_000) return result * 86_400_000;
  if (magnitude < 100_000_000_000) return result * 1000;
  while (Math.abs(result) > 8_640_000_000_000_000) result /= 1000;
  return result;
}

function numberValue(value: unknown): number {
  return value === null || value === undefined ? Number.NaN : Number(value);
}

function requiredNumber(value: unknown, name: string): number {
  const result = numberValue(value);
  if (!Number.isFinite(result)) throw new Error(`${name} must be numeric.`);
  return result;
}

async function disposeMounted(values: readonly MountedView[]): Promise<void> {
  const results = await Promise.allSettled(values.map((value) => Promise.resolve(value.dispose())));
  const rejected = results.find(
    (result): result is PromiseRejectedResult => result.status === "rejected",
  );
  if (rejected !== undefined) throw rejected.reason;
}

async function imageReady(image: HTMLImageElement, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  if (!image.complete) await image.decode();
  signal.throwIfAborted();
  if (image.naturalWidth === 0) throw new Error("The chart snapshot could not be decoded.");
}

function required<T extends Element>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (element === null) throw new Error(`${selector} is missing.`);
  return element;
}

window.addEventListener("pagehide", () => {
  revision += 1;
  active?.abort();
  void disposeMounted(mounted.splice(0));
});
