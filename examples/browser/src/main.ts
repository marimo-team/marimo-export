import {
  httpSource,
  openExport,
  type ExportScenario,
  type NotebookExport,
} from "@marimo-team/marimo-export";
import {
  anywidget,
  type LoadedAnyWidget,
  type MountedAnyWidget,
} from "@marimo-team/marimo-export-anywidget";
import {
  vegaLite,
  type MountedVegaLite,
  type VegaLiteChart,
} from "@marimo-team/marimo-export-vegalite";

import "./style.css";

interface ProjectedState {
  readonly accent: string;
  readonly child_count: number;
  readonly raw_count: number;
}

interface CounterState extends Record<string, unknown> {
  accent: string;
  count: number;
  label: string;
  payload: DataView;
}

interface DashboardState extends Record<string, unknown> {
  accent: string;
  child: string;
  title: string;
}

interface CounterExports {
  reset(): void;
}

interface DashboardExports {
  rename(title: string): void;
}

interface LoadedScenario {
  readonly chart: VegaLiteChart;
  readonly dashboard: LoadedAnyWidget<DashboardState, DashboardExports>;
  readonly projected: ProjectedState;
  readonly raw: LoadedAnyWidget<CounterState, CounterExports>;
}

interface MountedScenario {
  readonly raw: MountedAnyWidget<CounterState, CounterExports>;
  readonly dashboard: MountedAnyWidget<DashboardState, DashboardExports>;
  dispose(): Promise<void>;
}

const OUTPUT = {
  chart: ["chart", "vegalite"],
  dashboard: ["wrapped_dashboard", "anywidget"],
  projected: ["projected", "json"],
  raw: ["raw_counter", "anywidget"],
} as const;

const root = new URLSearchParams(location.search).get("export") ?? "/export/";
const app = required<HTMLElement>("#app");
const scenarioSelect = required<HTMLSelectElement>("[data-testid='scenario-select']");
const loadStatus = required<HTMLElement>("[data-testid='load-status']");
const projectedRawCount = required<HTMLElement>("[data-testid='projected-raw-count']");
const projectedChildCount = required<HTMLElement>("[data-testid='projected-child-count']");
const projectedAccent = required<HTMLElement>("[data-testid='projected-accent']");
const projectedJson = required<HTMLElement>("[data-testid='projected-json']");
const accentSwatch = required<HTMLElement>("[data-testid='accent-swatch']");
const chartHost = required<HTMLElement>("[data-testid='chart']");
const rawHost = required<HTMLElement>("[data-testid='raw-counter']");
const dashboardHost = required<HTMLElement>("[data-testid='wrapped-dashboard']");
const rawModelCount = required<HTMLOutputElement>("[data-testid='raw-model-count']");
const dashboardModelTitle = required<HTMLOutputElement>("[data-testid='dashboard-model-title']");
const bufferState = required<HTMLElement>("[data-testid='buffer-state']");
const rawIncrement = required<HTMLButtonElement>("[data-testid='raw-increment']");
const rawReset = required<HTMLButtonElement>("[data-testid='raw-reset']");
const dashboardRenameForm = required<HTMLFormElement>("[data-testid='dashboard-rename-form']");
const dashboardTitle = required<HTMLInputElement>("[data-testid='dashboard-title']");
const dashboardRename = required<HTMLButtonElement>("[data-testid='dashboard-rename']");
const cleanupCount = required<HTMLOutputElement>("[data-testid='cleanup-count']");
const cleanupLog = required<HTMLOListElement>("[data-testid='cleanup-log']");

let published: NotebookExport;
let mounted: MountedScenario | undefined;
let activeController: AbortController | undefined;
let requestedRevision = 0;
let transition = Promise.resolve();
const cleanupEvents: string[] = [];

const recordCleanup = ((event: CustomEvent<unknown>) => {
  const detail = cleanupDetail(event.detail);
  cleanupEvents.push(detail.widget);
  cleanupCount.value = String(cleanupEvents.length);
  cleanupCount.textContent = String(cleanupEvents.length);
  app.dataset.cleanupCount = String(cleanupEvents.length);

  const item = document.createElement("li");
  item.textContent = `${detail.widget} released`;
  item.dataset.widget = detail.widget;
  cleanupLog.prepend(item);
}) as EventListener;

rawIncrement.addEventListener("click", () => {
  const current = mounted?.raw;
  if (current === undefined) return;
  current.model.set("count", current.model.get("count") + 1);
  current.model.save_changes();
});

rawReset.addEventListener("click", () => {
  mounted?.raw.exports.reset();
});

dashboardRenameForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const title = dashboardTitle.value.trim();
  if (title.length === 0) {
    dashboardTitle.setCustomValidity("Enter a dashboard title.");
    dashboardTitle.reportValidity();
    return;
  }
  dashboardTitle.setCustomValidity("");
  mounted?.dashboard.exports.rename(title);
});

scenarioSelect.addEventListener("change", () => requestScenario(scenarioSelect.value));

window.addEventListener(
  "pagehide",
  () => {
    requestedRevision += 1;
    activeController?.abort();
    const current = mounted;
    mounted = undefined;
    if (current !== undefined) void current.dispose();
  },
  { once: true },
);

try {
  published = await openExport(httpSource(root));
  configureScenarios(published);
  requestScenario(
    published.scenarios().some((scenario) => scenario.id === "baseline")
      ? "baseline"
      : published.scenarios()[0]!.id,
  );
} catch (error) {
  showError(error);
}

function requestScenario(id: string): void {
  const revision = ++requestedRevision;
  activeController?.abort();
  setLoading(id);
  transition = transition.then(
    () => activateScenario(id, revision),
    () => activateScenario(id, revision),
  );
}

async function activateScenario(id: string, revision: number): Promise<void> {
  if (revision !== requestedRevision) return;

  let controller: AbortController | undefined;
  try {
    if (mounted !== undefined) {
      const previous = mounted;
      mounted = undefined;
      await previous.dispose();
    }
    if (revision !== requestedRevision) return;

    clearMounts();
    controller = new AbortController();
    activeController = controller;
    const scenario = published.scenario(id);
    const loaded = await loadScenario(scenario, controller.signal);
    controller.signal.throwIfAborted();

    let chart: MountedVegaLite | undefined;
    let raw: MountedAnyWidget<CounterState, CounterExports> | undefined;
    let dashboard: MountedAnyWidget<DashboardState, DashboardExports> | undefined;
    try {
      chart = await loaded.chart.mount(chartHost, { actions: false, renderer: "svg" });
      controller.signal.throwIfAborted();
      raw = await loaded.raw.mount(rawHost, { signal: controller.signal });
      controller.signal.throwIfAborted();
      dashboard = await loaded.dashboard.mount(dashboardHost, { signal: controller.signal });
      controller.signal.throwIfAborted();
    } catch (error) {
      const cancelled = controller.signal.aborted;
      try {
        await disposeResources(controller, chart, raw, dashboard);
      } catch (cleanupError) {
        throw new AggregateError([error, cleanupError], "Scenario mount and cleanup failed.");
      }
      if (cancelled && revision !== requestedRevision) return;
      throw error;
    }

    const next = createMountedScenario(controller, chart, raw, dashboard);
    if (revision !== requestedRevision) {
      await next.dispose();
      return;
    }

    try {
      displayScenario(scenario, loaded, next);
    } catch (error) {
      try {
        await next.dispose();
      } catch (cleanupError) {
        throw new AggregateError([error, cleanupError], "Scenario display and cleanup failed.");
      }
      throw error;
    }
    mounted = next;
    setReady(id);
  } catch (error) {
    controller?.abort();
    if (revision !== requestedRevision) return;
    showError(error);
  }
}

async function loadScenario(
  scenario: ExportScenario,
  signal: AbortSignal,
): Promise<LoadedScenario> {
  const [projected, chart, raw, dashboard] = await Promise.all([
    scenario.output(...OUTPUT.projected).json(projectedState, { signal }),
    scenario
      .output(...OUTPUT.chart)
      .load(vegaLite({ actions: false, renderer: "svg" }), { signal }),
    scenario.output(...OUTPUT.raw).load(anywidget<CounterState, CounterExports>(), { signal }),
    scenario
      .output(...OUTPUT.dashboard)
      .load(anywidget<DashboardState, DashboardExports>(), { signal }),
  ]);
  return { projected, chart, raw, dashboard };
}

function createMountedScenario(
  controller: AbortController,
  chart: MountedVegaLite,
  raw: MountedAnyWidget<CounterState, CounterExports>,
  dashboard: MountedAnyWidget<DashboardState, DashboardExports>,
): MountedScenario {
  let disposePromise: Promise<void> | undefined;
  return {
    raw,
    dashboard,
    dispose() {
      disposePromise ??= disposeResources(controller, chart, raw, dashboard);
      return disposePromise;
    },
  };
}

async function disposeResources(
  controller: AbortController,
  chart: MountedVegaLite | undefined,
  raw: MountedAnyWidget<CounterState, CounterExports> | undefined,
  dashboard: MountedAnyWidget<DashboardState, DashboardExports> | undefined,
): Promise<void> {
  controller.abort();
  const errors: unknown[] = [];
  for (const widget of [dashboard, raw]) {
    if (widget === undefined) continue;
    try {
      // Widget disposal is ordered so nested child views settle before the raw view.
      // oxlint-disable-next-line eslint/no-await-in-loop
      await widget.dispose();
    } catch (error) {
      errors.push(error);
    }
  }
  try {
    chart?.finalize();
  } catch (error) {
    errors.push(error);
  }
  if (errors.length > 0) throw new AggregateError(errors, "Scenario cleanup failed.");
}

function displayScenario(
  scenario: ExportScenario,
  loaded: LoadedScenario,
  current: MountedScenario,
): void {
  projectedRawCount.textContent = String(loaded.projected.raw_count);
  projectedChildCount.textContent = String(loaded.projected.child_count);
  projectedAccent.textContent = loaded.projected.accent;
  accentSwatch.style.backgroundColor = loaded.projected.accent;
  projectedJson.textContent = JSON.stringify(
    { scenario: scenario.id, inputs: scenario.inputs, projected: loaded.projected },
    null,
    2,
  );

  observeCleanup(rawHost);
  observeCleanup(dashboardHost);
  bufferState.textContent = describeBuffer(loaded.raw.initialState.payload);
  const updateRawCount = () => {
    const count = current.raw.model.get("count");
    rawModelCount.value = String(count);
    rawModelCount.textContent = String(count);
    rawHost.dataset.modelCount = String(count);
  };
  const updateDashboardTitle = () => {
    const title = current.dashboard.model.get("title");
    dashboardModelTitle.value = title;
    dashboardModelTitle.textContent = title;
    dashboardHost.dataset.modelTitle = title;
  };
  current.raw.model.on("change:count", updateRawCount);
  current.dashboard.model.on("change:title", updateDashboardTitle);
  updateRawCount();
  updateDashboardTitle();
  dashboardTitle.value = current.dashboard.model.get("title");

  rawIncrement.disabled = false;
  rawReset.disabled = false;
  dashboardRename.disabled = false;
}

function observeCleanup(host: HTMLElement): void {
  const widgetRoot = host.firstElementChild;
  if (widgetRoot === null) throw new Error("Mounted AnyWidget must render a root element.");
  widgetRoot.addEventListener("marimo-export-widget-cleanup", recordCleanup);
}

function configureScenarios(notebook: NotebookExport): void {
  const scenarios = notebook.scenarios();
  if (scenarios.length === 0) throw new Error("The publication contains no scenarios.");
  scenarioSelect.replaceChildren(
    ...scenarios.map((scenario) => {
      const option = document.createElement("option");
      option.value = scenario.id;
      option.textContent = humanize(scenario.id);
      return option;
    }),
  );
  scenarioSelect.disabled = false;
}

function clearMounts(): void {
  chartHost.replaceChildren();
  rawHost.replaceChildren();
  dashboardHost.replaceChildren();
  projectedRawCount.textContent = "–";
  projectedChildCount.textContent = "–";
  projectedAccent.textContent = "–";
  projectedJson.textContent = "Waiting for a scenario…";
  accentSwatch.style.removeProperty("background-color");
  rawHost.removeAttribute("data-model-count");
  dashboardHost.removeAttribute("data-model-title");
  rawModelCount.value = "";
  rawModelCount.textContent = "–";
  dashboardModelTitle.value = "";
  dashboardModelTitle.textContent = "–";
  bufferState.textContent = "Buffer state loads with the model.";
}

function setLoading(id: string): void {
  app.dataset.status = "loading";
  app.dataset.scenario = id;
  loadStatus.textContent = `Loading ${humanize(id)}…`;
  scenarioSelect.value = id;
  rawIncrement.disabled = true;
  rawReset.disabled = true;
  dashboardRename.disabled = true;
}

function setReady(id: string): void {
  app.dataset.status = "ready";
  app.dataset.scenario = id;
  loadStatus.textContent = `${humanize(id)} is mounted from static files.`;
}

function showError(error: unknown): void {
  clearMounts();
  app.dataset.status = "error";
  loadStatus.textContent = error instanceof Error ? error.message : String(error);
  scenarioSelect.disabled = published === undefined;
  rawIncrement.disabled = true;
  rawReset.disabled = true;
  dashboardRename.disabled = true;
}

function projectedState(value: unknown): ProjectedState {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("Projected state must be a JSON object.");
  }
  const record = value as Record<string, unknown>;
  if (typeof record.accent !== "string") {
    throw new TypeError("Projected state accent must be a string.");
  }
  if (typeof record.child_count !== "number" || !Number.isFinite(record.child_count)) {
    throw new TypeError("Projected state child_count must be a finite number.");
  }
  if (typeof record.raw_count !== "number" || !Number.isFinite(record.raw_count)) {
    throw new TypeError("Projected state raw_count must be a finite number.");
  }
  return Object.freeze({
    accent: record.accent,
    child_count: record.child_count,
    raw_count: record.raw_count,
  });
}

function describeBuffer(value: unknown): string {
  if (!(value instanceof DataView)) {
    throw new TypeError("Raw counter payload must decode to a DataView.");
  }
  const bytes = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  const checksum = bytes.reduce((total, byte) => total + byte, 0);
  return `Buffer bytes ${[...bytes].join(", ")} · checksum ${checksum}`;
}

function cleanupDetail(value: unknown): { readonly widget: string } {
  if (
    typeof value !== "object" ||
    value === null ||
    !("widget" in value) ||
    typeof value.widget !== "string"
  ) {
    return { widget: "widget" };
  }
  return { widget: value.widget };
}

function humanize(value: string): string {
  const label = value.replaceAll("_", " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function required<T extends Element>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (element === null) throw new Error(`${selector} is missing.`);
  return element;
}
