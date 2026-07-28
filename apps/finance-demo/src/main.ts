import {
  imageLoader,
  openPublication,
  PublicationError,
  scalarLoader,
} from "@marimo-team/marimo-export";
import type {
  JsonObject,
  JsonValue,
  MountedView,
  Publication,
  PublishedState,
} from "@marimo-team/marimo-export";
import { anyWidgetLoader } from "@marimo-team/marimo-export-loader-anywidget";
import { arrowTableLoader } from "@marimo-team/marimo-export-loader-arrow";
import { numpyLoader } from "@marimo-team/marimo-export-loader-numpy";
import { parquetRowsLoader } from "@marimo-team/marimo-export-loader-parquet";
import { vegaLiteLoader } from "@marimo-team/marimo-export-loader-vegalite";

import "./style.css";

interface Diagnostics {
  readonly currentState: string | null;
  readonly disposals: number;
  readonly errors: number;
  readonly mounts: number;
  readonly revision: number;
  readonly transitions: number;
}

declare global {
  interface Window {
    __MARIMO_EXPORT_DEMO__?: Diagnostics;
  }
}

const root = new URLSearchParams(location.search).get("publication") ?? "./publication/";
const app = required<HTMLElement>("#app");
const stateSelect = required<HTMLSelectElement>("#state-select");
const stateButtons = required<HTMLElement>("#state-buttons");
const unavailableButton = required<HTMLButtonElement>("#unavailable-state");
const unavailablePanel = required<HTMLElement>("#unavailable-panel");
const unavailableInputs = required<HTMLElement>("#unavailable-inputs");
const status = required<HTMLElement>("#status");
const verification = required<HTMLElement>("#verification");
const inputVector = required<HTMLElement>("#input-vector");
const rowCount = required<HTMLElement>("#row-count");
const arrowSummary = required<HTMLElement>("#arrow-summary");
const arrowFields = required<HTMLElement>("#arrow-fields");
const numpySummary = required<HTMLElement>("#numpy-summary");
const numpyValues = required<HTMLElement>("#numpy-values");
const parquetSummary = required<HTMLElement>("#parquet-summary");
const parquetFields = required<HTMLElement>("#parquet-fields");
const dashboardHost = required<HTMLElement>("#dashboard");
const vegaHost = required<HTMLElement>("#chart-vegalite");
const pngHost = required<HTMLElement>("#chart-png");
const errorPanel = required<HTMLElement>("#error-panel");
const errorMessage = required<HTMLElement>("#error-message");

let publication: Publication | undefined;
let mounted: MountedView[] = [];
let revision = 0;
let transitions = 0;
let mounts = 0;
let disposals = 0;
let errors = 0;
let currentState: string | null = null;
let active: AbortController | undefined;
let transition = Promise.resolve();

const diagnostics = Object.freeze({
  get currentState() {
    return currentState;
  },
  get disposals() {
    return disposals;
  },
  get errors() {
    return errors;
  },
  get mounts() {
    return mounts;
  },
  get revision() {
    return revision;
  },
  get transitions() {
    return transitions;
  },
});
window.__MARIMO_EXPORT_DEMO__ = diagnostics;

stateSelect.addEventListener("change", () => requestState(stateSelect.value));
unavailableButton.addEventListener("click", showUnavailableState);
window.addEventListener("pagehide", () => {
  revision += 1;
  active?.abort();
  active = undefined;
  void disposeMounted(mounted.splice(0));
});

try {
  publication = await openPublication(root);
  configureStates(publication);
  void verifyPublication(publication);
  requestState(publication.state("baseline").name);
} catch (error) {
  showError(error);
}

function requestState(name: string): void {
  const selected = publication?.state(name);
  if (selected === undefined) return;
  const nextRevision = ++revision;
  transitions += 1;
  active?.abort();
  const controller = new AbortController();
  active = controller;
  stateSelect.value = name;
  stateSelect.disabled = true;
  unavailablePanel.hidden = true;
  errorPanel.hidden = true;
  app.dataset.status = "loading";
  status.textContent = `Loading ${name}…`;
  updateStateButtons(name);
  transition = transition.then(
    () => renderState(selected, nextRevision, controller),
    () => renderState(selected, nextRevision, controller),
  );
}

async function renderState(
  state: PublishedState,
  nextRevision: number,
  controller: AbortController,
): Promise<void> {
  const { signal } = controller;
  if (nextRevision !== revision || signal.aborted) return;
  const previous = mounted.splice(0);
  await disposeMounted(previous);
  if (nextRevision !== revision || signal.aborted) return;
  clearMounts();

  const nextMounted: MountedView[] = [];
  try {
    const [scalar, matrix, arrow, parquet, chart, image, widget] = await Promise.all([
      state.output("row_count").load(scalarLoader(), { signal }),
      state.output("ohlc_matrix").load(numpyLoader(), { signal }),
      state.output("prices_arrow").load(arrowTableLoader(), { signal }),
      state.output("prices_parquet").load(parquetRowsLoader(), { signal }),
      state.output("chart_vegalite").load(vegaLiteLoader({ actions: false }), { signal }),
      state.output("chart_png").load(imageLoader(), { signal }),
      state.output("dashboard").load(anyWidgetLoader(), { signal }),
    ]);
    signal.throwIfAborted();

    nextMounted.push(await widget.mount(dashboardHost, { signal }));
    mounts += 1;
    signal.throwIfAborted();
    nextMounted.push(await chart.mount(vegaHost, { renderer: "svg", signal }));
    mounts += 1;
    signal.throwIfAborted();
    nextMounted.push(await image.mount(pngHost, { signal }));
    mounts += 1;
    signal.throwIfAborted();

    if (nextRevision !== revision) {
      await disposeMounted(nextMounted);
      return;
    }
    mounted = nextMounted;
    currentState = state.name;
    inputVector.textContent = stringify(state.inputs);
    rowCount.textContent = formatScalar(scalar);
    arrowSummary.textContent = `${arrow.numRows} rows × ${arrow.numCols} columns`;
    arrowFields.textContent = arrow.schema.fields.map((field) => field.name).join(", ");
    numpySummary.textContent = `${matrix.shape.join(" × ")} · ${matrix.dtype.descriptor}`;
    numpyValues.textContent = previewArray(matrix.data);
    parquetSummary.textContent = `${parquet.length} row objects`;
    parquetFields.textContent = Object.keys(parquet[0] ?? {}).join(", ");
    app.dataset.currentState = state.name;
    app.dataset.status = "ready";
    status.textContent = `${state.name} is mounted from verified static assets.`;
  } catch (error) {
    await disposeMounted(nextMounted);
    if (signal.aborted || nextRevision !== revision) return;
    showError(error);
  } finally {
    if (active === controller) active = undefined;
    if (nextRevision === revision) stateSelect.disabled = false;
  }
}

function configureStates(value: Publication): void {
  const states = value.states();
  if (states.length === 0) throw new Error("Publication has no states.");
  stateSelect.replaceChildren(
    ...states.map((state) => {
      const option = document.createElement("option");
      option.value = state.name;
      option.textContent = state.name;
      return option;
    }),
  );
  stateButtons.replaceChildren(
    ...states.map((state) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.state = state.name;
      button.textContent = state.name.replaceAll("_", " ");
      button.title = stringify(state.inputs);
      button.addEventListener("click", () => requestState(state.name));
      return button;
    }),
  );
  stateSelect.disabled = false;
  unavailableButton.disabled = false;
}

function updateStateButtons(name: string): void {
  for (const button of stateButtons.querySelectorAll<HTMLButtonElement>("button")) {
    button.dataset.active = String(button.dataset.state === name);
  }
}

async function verifyPublication(value: Publication): Promise<void> {
  try {
    const result = await value.verify();
    verification.textContent = `${result.assets} unique assets · ${formatBytes(result.bytesVerified)} verified`;
    verification.dataset.status = "verified";
  } catch (error) {
    verification.textContent =
      error instanceof Error ? `Verification failed: ${error.message}` : "Verification failed";
    verification.dataset.status = "error";
  }
}

function showUnavailableState(): void {
  const value = publication;
  if (value === undefined) return;
  for (const state of value.states()) {
    for (const name of value.inputNames) {
      const candidate = structuredClone(state.inputs) as Record<string, JsonValue>;
      candidate[name] = unpublishedValue(candidate[name]!);
      try {
        value.resolve(candidate);
      } catch (error) {
        if (error instanceof PublicationError && error.code === "state_unavailable") {
          unavailableInputs.textContent = stringify(candidate);
          unavailablePanel.hidden = false;
          unavailablePanel.focus();
          return;
        }
        throw error;
      }
    }
  }
  throw new Error("Could not construct an unavailable input vector.");
}

function unpublishedValue(value: JsonValue): JsonValue {
  if (value === null) return "__unpublished__";
  if (typeof value === "boolean") return !value;
  if (typeof value === "string") return `${value}__unpublished__`;
  if (typeof value === "number") {
    const candidate = value + 0.125;
    return Number.isFinite(candidate) ? candidate : value - 0.125;
  }
  if (Array.isArray(value)) return [...value, "__unpublished__"];
  return { ...(value as JsonObject), __marimo_export_unpublished__: true };
}

async function disposeMounted(values: readonly MountedView[]): Promise<void> {
  const results = await Promise.allSettled(values.map((value) => Promise.resolve(value.dispose())));
  disposals += values.length;
  const rejected = results.find(
    (result): result is PromiseRejectedResult => result.status === "rejected",
  );
  if (rejected !== undefined) throw rejected.reason;
}

function clearMounts(): void {
  dashboardHost.replaceChildren();
  vegaHost.replaceChildren();
  pngHost.replaceChildren();
}

function showError(error: unknown): void {
  errors += 1;
  app.dataset.status = "error";
  errorPanel.hidden = false;
  errorMessage.textContent = error instanceof Error ? error.message : String(error);
  status.textContent = "Publication load failed.";
  stateSelect.disabled = publication === undefined;
}

function previewArray(value: ArrayBufferView): string {
  if (!("length" in value) || typeof value.length !== "number") return value.constructor.name;
  const sequence = value as unknown as ArrayLike<unknown>;
  return Array.from({ length: Math.min(sequence.length, 8) }, (_, index) =>
    String(sequence[index]),
  ).join(", ");
}

function formatScalar(value: null | boolean | string | number | bigint): string {
  return typeof value === "bigint" ? value.toString() : String(value);
}

function stringify(value: JsonObject): string {
  return JSON.stringify(value, null, 2);
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function required<T extends Element>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (element === null) throw new Error(`${selector} is missing.`);
  return element;
}
