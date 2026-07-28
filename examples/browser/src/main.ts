import {
  openPublication,
  type JsonValue,
  type MountedView,
  type Publication,
} from "@marimo-team/marimo-export";
import { vegaLiteLoader } from "@marimo-team/marimo-export-loader-vegalite";

import "./style.css";

interface Summary {
  readonly latestClose: Readonly<Record<string, number>>;
  readonly rows: number;
  readonly symbols: readonly string[];
  readonly windowDays: number;
}

const root = new URLSearchParams(location.search).get("publication") ?? "/publication/";
const app = required<HTMLElement>("#app");
const variantSelect = required<HTMLSelectElement>("#variant");
const status = required<HTMLElement>("#status");
const symbols = required<HTMLElement>("#symbols");
const rows = required<HTMLElement>("#rows");
const latestClose = required<HTMLElement>("#latest-close");
const summaryJson = required<HTMLElement>("#summary-json");
const chart = required<HTMLElement>("#chart");

let publication: Publication;
let mounted: MountedView | undefined;
let revision = 0;
let transition = Promise.resolve();
let activeTransition: AbortController | undefined;

variantSelect.addEventListener("change", () => requestVariant(variantSelect.value));
window.addEventListener("pagehide", () => {
  revision += 1;
  activeTransition?.abort();
  activeTransition = undefined;
  const current = mounted;
  mounted = undefined;
  if (current !== undefined) void current.dispose();
});

try {
  publication = await openPublication(root, {
    loaders: [vegaLiteLoader({ actions: false, renderer: "svg" })],
  });
  configureVariants(publication);
  requestVariant(publication.variant("current").name);
} catch (error) {
  showError(error);
}

function requestVariant(name: string): void {
  const nextRevision = ++revision;
  activeTransition?.abort();
  const controller = new AbortController();
  activeTransition = controller;
  variantSelect.value = name;
  variantSelect.disabled = true;
  app.dataset.status = "loading";
  status.textContent = `Loading ${name}…`;
  transition = transition.then(
    () => showVariant(name, nextRevision, controller),
    () => showVariant(name, nextRevision, controller),
  );
}

async function showVariant(
  name: string,
  nextRevision: number,
  controller: AbortController,
): Promise<void> {
  const { signal } = controller;
  if (nextRevision !== revision || signal.aborted) return;

  let nextMounted: MountedView | undefined;
  try {
    const previous = mounted;
    mounted = undefined;
    if (previous !== undefined) await previous.dispose();
    if (nextRevision !== revision || signal.aborted) return;

    chart.replaceChildren();
    const variant = publication.variant(name);
    const summaryValue = await variant.output("summary").format("json").json({ signal });
    const summary = parseSummary(summaryValue);
    if (nextRevision !== revision || signal.aborted) return;

    nextMounted = await variant.output("chart").format("vegalite").mount(chart, { signal });
    if (nextRevision !== revision || signal.aborted) {
      await nextMounted.dispose();
      return;
    }

    mounted = nextMounted;
    nextMounted = undefined;
    displaySummary(summary, summaryValue);
    app.dataset.status = "ready";
    status.textContent = `${name} is loaded from the static publication.`;
  } catch (error) {
    if (nextMounted !== undefined) await nextMounted.dispose();
    if (signal.aborted) return;
    showError(error);
  } finally {
    if (activeTransition === controller) activeTransition = undefined;
    if (nextRevision === revision) variantSelect.disabled = false;
  }
}

function configureVariants(value: Publication): void {
  const variants = value.variants();
  if (variants.length === 0) throw new Error("The publication contains no variants.");
  variantSelect.replaceChildren(
    ...variants.map((variant) => {
      const option = document.createElement("option");
      option.value = variant.name;
      option.textContent = variant.name;
      return option;
    }),
  );
}

function displaySummary(value: Summary, raw: JsonValue): void {
  symbols.textContent = value.symbols.join(", ") || "None";
  rows.textContent = String(value.rows);
  latestClose.replaceChildren(
    ...Object.entries(value.latestClose).map(([symbol, close]) => {
      const item = document.createElement("li");
      item.textContent = `${symbol} ${close.toFixed(2)}`;
      return item;
    }),
  );
  summaryJson.textContent = JSON.stringify(raw, null, 2);
}

function parseSummary(value: JsonValue): Summary {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("The summary output must be a JSON object.");
  }
  const record = value as Readonly<Record<string, JsonValue>>;
  const symbols = record.symbols;
  const rows = record.rows;
  const windowDays = record.window_days;
  const latestClose = record.latest_close;
  if (!Array.isArray(symbols) || symbols.some((symbol) => typeof symbol !== "string")) {
    throw new TypeError("summary.symbols must be an array of strings.");
  }
  if (typeof rows !== "number" || !Number.isSafeInteger(rows)) {
    throw new TypeError("summary.rows must be an integer.");
  }
  if (typeof windowDays !== "number" || !Number.isSafeInteger(windowDays)) {
    throw new TypeError("summary.window_days must be an integer.");
  }
  if (latestClose === null || typeof latestClose !== "object" || Array.isArray(latestClose)) {
    throw new TypeError("summary.latest_close must be a JSON object.");
  }
  const closes: Record<string, number> = {};
  for (const [symbol, close] of Object.entries(
    latestClose as Readonly<Record<string, JsonValue>>,
  )) {
    if (typeof close !== "number" || !Number.isFinite(close)) {
      throw new TypeError(`summary.latest_close.${symbol} must be a finite number.`);
    }
    closes[symbol] = close;
  }
  return {
    latestClose: Object.freeze(closes),
    rows,
    symbols: Object.freeze([...symbols] as string[]),
    windowDays,
  };
}

function showError(error: unknown): void {
  app.dataset.status = "error";
  status.textContent = error instanceof Error ? error.message : String(error);
  variantSelect.disabled = publication === undefined;
}

function required<T extends Element>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (element === null) throw new Error(`${selector} is missing.`);
  return element;
}
