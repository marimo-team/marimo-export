import {
  openExport,
  type ExportState,
  type JsonObject,
  type JsonValue,
  type NotebookExport,
} from "@marimo-team/marimo-export";
import { jsonLoader } from "@marimo-team/marimo-export/loader/json";
import { marimoOutputLoader } from "@marimo-team/marimo-export/loader/marimo-output";

import "./style.css";

type StateName = "weekly" | "monthly";

interface Summary {
  readonly days: number;
  readonly label: string;
}

const app = required<HTMLElement>("#app");
const dayCount = required<HTMLElement>("#day-count");
const errorPanel = required<HTMLElement>("#error-panel");
const reportHost = required<HTMLElement>("#report-output");
const stateLabel = required<HTMLElement>("#state-label");
const status = required<HTMLElement>("#status");
const summaryHost = required<HTMLElement>("#summary-json");
const summaryLabel = required<HTMLElement>("#summary-label");
const stateButtons = [...document.querySelectorAll<HTMLButtonElement>("[data-state]")];

let notebookExport: NotebookExport | undefined;
let active: AbortController | undefined;
let revision = 0;

void start();

async function start(): Promise<void> {
  try {
    const opened = await openExport("./export/");
    await opened.verify();
    notebookExport = opened;
    for (const button of stateButtons) {
      button.addEventListener("click", () => selectState(stateName(button.dataset.state)));
    }
    selectState(stateFromHash(opened));
  } catch (error) {
    console.error(error);
    showError();
  }
}

function selectState(name: StateName): void {
  const state = notebookExport?.state(name);
  if (state === undefined) return;
  const currentRevision = ++revision;
  active?.abort();
  const controller = new AbortController();
  active = controller;
  setBusy(name, true);
  history.replaceState(null, "", `#${name}`);
  void renderState(name, state, currentRevision, controller);
}

async function renderState(
  name: StateName,
  state: ExportState,
  currentRevision: number,
  controller: AbortController,
): Promise<void> {
  const { signal } = controller;
  try {
    const [summaryValue, report] = await Promise.all([
      state.output("summary").load(jsonLoader(), { signal }),
      state.output("report").load(marimoOutputLoader(), { signal }),
    ]);
    signal.throwIfAborted();
    if (currentRevision !== revision) return;
    const summary = parseSummary(summaryValue);
    if (
      report.output === null ||
      report.output.mimetype !== "text/markdown" ||
      !isStringValue(report.output.data)
    ) {
      throw new Error("The prepared report has an unexpected output.");
    }
    const parsed = new DOMParser().parseFromString(report.output.data, "text/html");
    dayCount.textContent = String(summary.days);
    stateLabel.textContent = `${name === "weekly" ? "Weekly" : "Monthly"} state`;
    summaryLabel.textContent = summary.label;
    summaryHost.textContent = JSON.stringify(summary, null, 2);
    reportHost.replaceChildren(...parsed.body.childNodes);
    errorPanel.hidden = true;
    setBusy(name, false);
  } catch (error) {
    if (!signal.aborted && currentRevision === revision) {
      console.error(error);
      showError();
    }
  } finally {
    if (active === controller) active = undefined;
  }
}

function setBusy(name: StateName, busy: boolean): void {
  app.setAttribute("aria-busy", String(busy));
  for (const button of stateButtons) {
    const selected = button.dataset.state === name;
    button.setAttribute("aria-pressed", String(selected));
  }
  status.textContent = busy ? `Loading ${name}` : `${name} report ready`;
}

function stateFromHash(value: NotebookExport): StateName {
  const name = location.hash.slice(1);
  if (name === "monthly" && hasState(value, name)) return name;
  return "weekly";
}

function stateName(value: string | undefined): StateName {
  return value === "monthly" ? "monthly" : "weekly";
}

function hasState(value: NotebookExport, name: StateName): boolean {
  try {
    value.state(name);
    return true;
  } catch {
    return false;
  }
}

function parseSummary(value: JsonValue): Summary {
  if (!isJsonObject(value)) throw new Error("The prepared summary must be an object.");
  const keys = Object.keys(value);
  if (
    keys.length !== 2 ||
    !keys.includes("days") ||
    !keys.includes("label") ||
    !isNumberValue(value.days) ||
    !Number.isInteger(value.days) ||
    !isStringValue(value.label) ||
    value.label !== `Last ${value.days} days`
  ) {
    throw new Error("The prepared summary has an unexpected shape.");
  }
  return Object.freeze({ days: value.days, label: value.label });
}

function isJsonObject(value: JsonValue): value is JsonObject {
  return (
    value !== null &&
    !Array.isArray(value) &&
    Object.prototype.toString.call(value) === "[object Object]"
  );
}

function isNumberValue(value: JsonValue | undefined): value is number {
  return isPrimitiveWithPrototype(value, Number.prototype);
}

function isStringValue(value: JsonValue | undefined): value is string {
  return isPrimitiveWithPrototype(value, String.prototype);
}

function isPrimitiveWithPrototype<Value, Prototype>(value: Value, prototype: Prototype): boolean {
  const boxed = Object(value);
  return boxed !== value && Object.getPrototypeOf(boxed) === prototype;
}

function showError(): void {
  app.setAttribute("aria-busy", "false");
  errorPanel.hidden = false;
  status.textContent = "The prepared report could not be opened";
}

function required<Element extends HTMLElement>(selector: string): Element {
  const element = document.querySelector<Element>(selector);
  if (element === null) throw new Error(`Required element ${selector} is missing.`);
  return element;
}
