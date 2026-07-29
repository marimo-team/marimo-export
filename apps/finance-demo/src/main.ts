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
  PublishedOutput,
  PublishedState,
  ScalarValue,
} from "@marimo-team/marimo-export";
import { anyWidgetLoader } from "@marimo-team/marimo-export/loader/anywidget";
import { arrowTableLoader } from "@marimo-team/marimo-export/loader/arrow";
import { numpyLoader } from "@marimo-team/marimo-export/loader/numpy";
import { parquetRowsLoader } from "@marimo-team/marimo-export/loader/parquet";
import { vegaLiteLoader } from "@marimo-team/marimo-export/loader/vegalite";

import "./style.css";

interface DomainFacts {
  readonly arrowRows: number;
  readonly dateMax: string;
  readonly dateMin: string;
  readonly numpyDtype: string;
  readonly numpyMax: number;
  readonly numpyMin: number;
  readonly numpyShape: readonly number[];
  readonly parquetRows: number;
  readonly rowCount: number;
  readonly symbols: readonly string[];
}

interface Diagnostics {
  readonly currentPublication: string | null;
  readonly currentState: string | null;
  readonly disposals: number;
  readonly errors: number;
  readonly facts: DomainFacts | null;
  readonly mounts: number;
  readonly revision: number;
  readonly transitions: number;
  readonly vegaPulses: number;
}

interface ArrowTableValue {
  readonly numCols: number;
  readonly numRows: number;
  readonly schema: {
    readonly fields: readonly { readonly name: string }[];
  };
  toArray(): readonly Record<string, unknown>[];
}

interface NumpyValue {
  readonly data: ArrayBufferView;
  readonly dtype: { readonly descriptor: string };
  readonly fortranOrder: boolean;
  readonly shape: readonly number[];
}

interface VegaViewValue {
  runAsync(): Promise<unknown>;
  signal(name: string): unknown;
  signal(name: string, value: unknown): VegaViewValue;
}

declare global {
  interface Window {
    __MARIMO_EXPORT_DEMO__?: Diagnostics;
  }
}

const search = new URLSearchParams(location.search);
const explicitRoot = search.get("publication");
const app = required<HTMLElement>("#app");
const ownershipSelect = required<HTMLSelectElement>("#ownership-select");
const runSelect = required<HTMLSelectElement>("#run-select");
const publicationPath = required<HTMLElement>("#publication-path");
const stateSelect = required<HTMLSelectElement>("#state-select");
const stateButtons = required<HTMLElement>("#state-buttons");
const sparsePatch = required<HTMLElement>("#sparse-patch");
const unavailableButton = required<HTMLButtonElement>("#unavailable-state");
const unavailablePanel = required<HTMLElement>("#unavailable-panel");
const unavailableInputs = required<HTMLElement>("#unavailable-inputs");
const status = required<HTMLElement>("#status");
const verification = required<HTMLElement>("#verification");
const producer = required<HTMLElement>("#producer");
const fingerprint = required<HTMLElement>("#fingerprint");
const outputMetadata = required<HTMLElement>("#output-metadata");
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
const vegaSignalButton = required<HTMLButtonElement>("#vega-signal");
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
let currentPublication: string | null = null;
let currentState: string | null = null;
let facts: DomainFacts | null = null;
let vegaView: VegaViewValue | undefined;
let vegaPulses = 0;
let active: AbortController | undefined;
let transition = Promise.resolve();

const diagnostics = Object.freeze({
  get currentPublication() {
    return currentPublication;
  },
  get currentState() {
    return currentState;
  },
  get disposals() {
    return disposals;
  },
  get errors() {
    return errors;
  },
  get facts() {
    return facts;
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
  get vegaPulses() {
    return vegaPulses;
  },
});
window.__MARIMO_EXPORT_DEMO__ = diagnostics;

if (explicitRoot !== null) {
  ownershipSelect.disabled = true;
  runSelect.disabled = true;
  publicationPath.textContent = explicitRoot;
} else {
  ownershipSelect.disabled = false;
  runSelect.disabled = false;
}

ownershipSelect.addEventListener("change", requestPublication);
runSelect.addEventListener("change", requestPublication);
stateSelect.addEventListener("change", () => requestState(stateSelect.value));
unavailableButton.addEventListener("click", showUnavailableState);
vegaSignalButton.addEventListener("click", () => {
  void pulseVegaSignal().catch(showError);
});
window.addEventListener("pagehide", () => {
  revision += 1;
  active?.abort();
  active = undefined;
  void disposeMounted(mounted.splice(0));
});

requestPublication();

function requestPublication(): void {
  const root = publicationRoot();
  const key =
    explicitRoot === null ? `${ownershipSelect.value}-${runSelect.value}` : "custom-publication";
  const nextRevision = ++revision;
  transitions += 1;
  active?.abort();
  const controller = new AbortController();
  active = controller;
  publication = undefined;
  currentPublication = null;
  currentState = null;
  facts = null;
  stateSelect.disabled = true;
  unavailableButton.disabled = true;
  errorPanel.hidden = true;
  unavailablePanel.hidden = true;
  app.dataset.status = "loading";
  delete app.dataset.currentState;
  delete app.dataset.currentPublication;
  publicationPath.textContent = root;
  verification.dataset.status = "pending";
  verification.textContent = "Integrity verification pending";
  status.textContent = `Opening ${key}…`;
  transition = transition.then(
    () => openAndRenderPublication(root, key, nextRevision, controller),
    () => openAndRenderPublication(root, key, nextRevision, controller),
  );
}

async function openAndRenderPublication(
  root: string,
  key: string,
  nextRevision: number,
  controller: AbortController,
): Promise<void> {
  const { signal } = controller;
  if (nextRevision !== revision || signal.aborted) return;
  const previous = mounted.splice(0);
  await disposeMounted(previous);
  if (nextRevision !== revision || signal.aborted) return;
  clearMounts();
  try {
    const value = await openPublication(root, { signal });
    status.textContent = `Verifying ${key}…`;
    const result = await value.verify({ signal });
    signal.throwIfAborted();
    if (nextRevision !== revision) return;
    publication = value;
    currentPublication = key;
    app.dataset.currentPublication = key;
    verification.textContent = `${result.assets} unique assets · ${formatBytes(result.bytesVerified)} verified`;
    verification.dataset.status = "verified";
    producer.textContent = `marimo ${value.producer.marimo} · marimo-export ${value.producer.marimoExport}`;
    configureStates(value);
    await renderState(value.state("baseline"), nextRevision, controller);
  } catch (error) {
    if (signal.aborted || nextRevision !== revision) return;
    verification.dataset.status = "error";
    verification.textContent =
      error instanceof Error ? `Verification failed: ${error.message}` : "Verification failed";
    showError(error);
  } finally {
    if (active === controller) active = undefined;
  }
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
    const chartMount = await chart.mount(vegaHost, { renderer: "svg", signal });
    nextMounted.push(chartMount);
    mounts += 1;
    vegaView = chartMount.result.view;
    vegaSignalButton.disabled = false;
    signal.throwIfAborted();
    nextMounted.push(await image.mount(pngHost, { signal }));
    mounts += 1;
    await imageReady(required<HTMLImageElement>("#chart-png img"), signal);

    if (nextRevision !== revision) {
      await disposeMounted(nextMounted);
      return;
    }
    mounted = nextMounted;
    facts = validateRepresentations(
      scalar,
      matrix as NumpyValue,
      arrow as ArrowTableValue,
      parquet,
    );
    currentState = state.name;
    inputVector.textContent = stringify(state.inputs);
    rowCount.textContent = formatScalar(scalar);
    arrowSummary.textContent = `${arrow.numRows} rows × ${arrow.numCols} columns`;
    arrowFields.textContent = arrow.schema.fields.map((field) => field.name).join(", ");
    numpySummary.textContent = `${matrix.shape.join(" × ")} · ${matrix.dtype.descriptor}`;
    numpyValues.textContent = `${previewArray(matrix.data)} · range ${formatRange(facts)}`;
    parquetSummary.textContent = `${parquet.length} row objects`;
    parquetFields.textContent = Object.keys(parquet[0] ?? {}).join(", ");
    renderStateMetadata(state);
    app.dataset.currentState = state.name;
    app.dataset.status = "ready";
    app.dataset.facts = JSON.stringify(facts);
    stateSelect.disabled = false;
    unavailableButton.disabled = false;
    status.textContent = `${state.name} is mounted from verified static assets.`;
  } catch (error) {
    await disposeMounted(nextMounted);
    if (signal.aborted || nextRevision !== revision) return;
    showError(error);
  } finally {
    if (active === controller) active = undefined;
    if (nextRevision === revision && publication !== undefined) stateSelect.disabled = false;
  }
}

function configureStates(value: Publication): void {
  const states = value.states();
  if (states.length === 0) throw new Error("Publication has no states.");
  const baseline = value.state("baseline");
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
      const patch = inputPatch(baseline.inputs, state.inputs);
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.state = state.name;
      button.dataset.patch = JSON.stringify(patch);
      button.textContent = state.name.replaceAll("_", " ");
      button.title = stringify(patch);
      button.addEventListener("click", () => {
        const resolved = baseline.resolve(patch);
        if (resolved.name !== state.name) {
          throw new Error(`Sparse patch resolved ${resolved.name} instead of ${state.name}.`);
        }
        sparsePatch.textContent = stringify(patch);
        requestState(resolved.name);
      });
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

function renderStateMetadata(state: PublishedState): void {
  fingerprint.textContent = state.fingerprint;
  const baseline = state.publication.state("baseline");
  sparsePatch.textContent = stringify(inputPatch(baseline.inputs, state.inputs));
  outputMetadata.replaceChildren(...state.outputs().map((output) => outputMetadataRow(output)));
}

function outputMetadataRow(output: PublishedOutput): HTMLElement {
  const row = document.createElement("div");
  row.className = "metadata-row";
  row.dataset.output = output.name;
  const name = document.createElement("strong");
  name.textContent = output.name;
  const representation = document.createElement("span");
  representation.textContent = `${output.codec} · ${output.mediaType.raw}`;
  const digest = document.createElement("code");
  digest.textContent = "asset" in output.descriptor ? output.descriptor.asset.sha256 : "inline";
  row.append(name, representation, digest);
  return row;
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
          unavailablePanel.dataset.errorCode = error.code;
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

function validateRepresentations(
  scalar: ScalarValue,
  matrix: NumpyValue,
  arrow: ArrowTableValue,
  parquet: readonly Record<string, unknown>[],
): DomainFacts {
  const expectedRows = scalarCount(scalar);
  const arrowRows = arrow.toArray();
  if (arrowRows.length !== expectedRows || arrow.numRows !== expectedRows) {
    throw new Error("Arrow row count disagrees with the scalar projection.");
  }
  if (parquet.length !== expectedRows) {
    throw new Error("Parquet row count disagrees with the scalar projection.");
  }
  if (matrix.shape.length !== 2 || matrix.shape[0] !== expectedRows || matrix.shape[1] !== 4) {
    throw new Error("NumPy shape disagrees with the table projections.");
  }

  const arrowSymbols = uniqueStrings(arrowRows.map((row) => row.Symbol));
  const parquetSymbols = uniqueStrings(parquet.map((row) => row.Symbol));
  if (JSON.stringify(arrowSymbols) !== JSON.stringify(parquetSymbols)) {
    throw new Error("Arrow and Parquet symbol domains disagree.");
  }
  const arrowDates = dateRange(arrowRows.map((row) => row.Date));
  const parquetDates = dateRange(parquet.map((row) => row.Date));
  if (arrowDates.minimum !== parquetDates.minimum || arrowDates.maximum !== parquetDates.maximum) {
    throw new Error("Arrow and Parquet date bounds disagree.");
  }

  const values = matrix.data as unknown as ArrayLike<number | bigint>;
  const fields = ["Open", "High", "Low", "Close"] as const;
  const numeric: number[] = [];
  for (let rowIndex = 0; rowIndex < arrowRows.length; rowIndex += 1) {
    for (const [columnIndex, field] of fields.entries()) {
      const expected = numberValue(arrowRows[rowIndex]![field]);
      const valueIndex = matrix.fortranOrder
        ? columnIndex * arrowRows.length + rowIndex
        : rowIndex * fields.length + columnIndex;
      const actual = Number(values[valueIndex]);
      if (!sameNumber(actual, expected)) {
        throw new Error("NumPy OHLC values disagree with Arrow.");
      }
      if (Number.isFinite(actual)) numeric.push(actual);
    }
  }
  if (numeric.length === 0) throw new Error("NumPy OHLC output has no finite values.");

  return Object.freeze({
    arrowRows: arrowRows.length,
    dateMax: new Date(arrowDates.maximum).toISOString(),
    dateMin: new Date(arrowDates.minimum).toISOString(),
    numpyDtype: matrix.dtype.descriptor,
    numpyMax: Math.max(...numeric),
    numpyMin: Math.min(...numeric),
    numpyShape: Object.freeze([...matrix.shape]),
    parquetRows: parquet.length,
    rowCount: expectedRows,
    symbols: Object.freeze(arrowSymbols),
  });
}

function publicationRoot(): string {
  return explicitRoot ?? `./publications/${ownershipSelect.value}-${runSelect.value}/`;
}

function inputPatch(baseline: JsonObject, target: JsonObject): JsonObject {
  return Object.freeze(
    Object.fromEntries(
      Object.keys(target)
        .filter((name) => JSON.stringify(target[name]) !== JSON.stringify(baseline[name]))
        .map((name) => [name, target[name]!]),
    ),
  );
}

function scalarCount(value: ScalarValue): number {
  if (typeof value !== "number" && typeof value !== "bigint") {
    throw new Error("Row count output must be numeric.");
  }
  const result = Number(value);
  if (!Number.isSafeInteger(result) || result <= 0) {
    throw new Error("Row count output must be a positive safe integer.");
  }
  return result;
}

function uniqueStrings(values: readonly unknown[]): string[] {
  return [...new Set(values.map((value) => String(value)))].sort();
}

function dateRange(values: readonly unknown[]): { minimum: number; maximum: number } {
  const dates = values.map(epochMilliseconds);
  const minimum = Math.min(...dates);
  const maximum = Math.max(...dates);
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) {
    throw new Error("Table date bounds are invalid.");
  }
  return { minimum, maximum };
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
  if (value === null || value === undefined) return Number.NaN;
  return Number(value);
}

function sameNumber(left: number, right: number): boolean {
  if (Number.isNaN(left) && Number.isNaN(right)) return true;
  if (!Number.isFinite(left) || !Number.isFinite(right)) return left === right;
  const scale = Math.max(1, Math.abs(left), Math.abs(right));
  return Math.abs(left - right) <= scale * 1e-12;
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
  vegaView = undefined;
  vegaSignalButton.disabled = true;
  dashboardHost.replaceChildren();
  vegaHost.replaceChildren();
  pngHost.replaceChildren();
}

async function pulseVegaSignal(): Promise<void> {
  const view = vegaView;
  if (view === undefined) return;
  vegaSignalButton.disabled = true;
  try {
    let widthSignal: { name: string; value: number } | undefined;
    for (const name of ["child_width", "width"]) {
      try {
        const value = Number(view.signal(name));
        if (Number.isFinite(value) && value > 1) {
          widthSignal = { name, value };
          break;
        }
      } catch {
        continue;
      }
    }
    if (widthSignal === undefined) {
      throw new Error("Vega-Lite width signal is unavailable.");
    }
    await view.signal(widthSignal.name, widthSignal.value - 1).runAsync();
    await view.signal(widthSignal.name, widthSignal.value).runAsync();
    vegaPulses += 1;
    vegaSignalButton.dataset.pulses = String(vegaPulses);
  } finally {
    vegaSignalButton.disabled = false;
  }
}

function showError(error: unknown): void {
  errors += 1;
  facts = null;
  app.dataset.status = "error";
  delete app.dataset.facts;
  errorPanel.hidden = false;
  errorPanel.dataset.errorCode = error instanceof PublicationError ? error.code : "unexpected";
  errorMessage.textContent = error instanceof Error ? error.message : String(error);
  status.textContent = "Publication load failed.";
  stateSelect.disabled = publication === undefined;
}

async function imageReady(image: HTMLImageElement, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  if (image.complete) {
    if (image.naturalWidth === 0 || image.naturalHeight === 0) {
      throw new Error("PNG image did not decode.");
    }
    return;
  }
  await new Promise<void>((resolve, reject) => {
    const dispose = () => {
      image.removeEventListener("load", loaded);
      image.removeEventListener("error", failed);
      signal.removeEventListener("abort", aborted);
    };
    const loaded = () => {
      dispose();
      resolve();
    };
    const failed = () => {
      dispose();
      reject(new Error("PNG image did not decode."));
    };
    const aborted = () => {
      dispose();
      reject(signal.reason);
    };
    image.addEventListener("load", loaded, { once: true });
    image.addEventListener("error", failed, { once: true });
    signal.addEventListener("abort", aborted, { once: true });
  });
  signal.throwIfAborted();
}

function previewArray(value: ArrayBufferView): string {
  if (!("length" in value) || typeof value.length !== "number") return value.constructor.name;
  const sequence = value as unknown as ArrayLike<unknown>;
  return Array.from({ length: Math.min(sequence.length, 8) }, (_, index) =>
    String(sequence[index]),
  ).join(", ");
}

function formatScalar(value: ScalarValue): string {
  return typeof value === "bigint" ? value.toString() : String(value);
}

function formatRange(value: DomainFacts): string {
  return `${value.numpyMin.toFixed(2)} to ${value.numpyMax.toFixed(2)}`;
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
