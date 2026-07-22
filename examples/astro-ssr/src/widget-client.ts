import { anywidget } from "@marimo-team/marimo-export-anywidget";
import { httpSource, openExport } from "@marimo-team/marimo-export";

import type { MountedAnyWidget } from "@marimo-team/marimo-export-anywidget";
import type { CounterState } from "./widget-state.js";

const root = document.querySelector<HTMLElement>("[data-anywidget-example]");

if (root !== null) {
  void mountCounter(root);
}

async function mountCounter(root: HTMLElement): Promise<void> {
  const host = requireElement<HTMLElement>(root, "[data-widget-host]");
  const control = requireElement<HTMLButtonElement>(root, "[data-widget-control]");
  const count = requireElement<HTMLOutputElement>(root, "[data-widget-count]");
  const status = requireElement<HTMLElement>(root, "[data-widget-status-text]");
  const error = requireElement<HTMLElement>(root, "[data-widget-error]");
  const scenarioId = root.dataset.scenario ?? "baseline";
  const controller = new AbortController();
  let mounted: MountedAnyWidget<CounterState> | undefined;
  let controlsBound = false;

  const showCount = () => {
    if (mounted === undefined || controller.signal.aborted) return;
    const current = mounted.model.get("count");
    count.value = String(current);
    root.dataset.widgetCount = String(current);
  };
  const addFive = () => {
    if (mounted === undefined || controller.signal.aborted) return;
    mounted.model.set("count", mounted.model.get("count") + 5);
    mounted.model.save_changes();
  };
  const releaseMounted = async () => {
    control.disabled = true;
    if (controlsBound) {
      control.removeEventListener("click", addFive);
      mounted?.model.off("change:count", showCount);
      controlsBound = false;
    }
    const current = mounted;
    mounted = undefined;
    await current?.dispose();
  };
  const showError = (cause: unknown, label: string) => {
    error.textContent = cause instanceof Error ? cause.message : String(cause);
    error.hidden = false;
    setStatus(root, status, "error", label);
  };
  const onPageHide = () => {
    controller.abort(new DOMException("The Astro page was hidden.", "AbortError"));
    void releaseMounted().then(
      () => setStatus(root, status, "disposed", "Disposed"),
      (cause: unknown) => showError(cause, "Disposal failed"),
    );
  };

  window.addEventListener("pagehide", onPageHide, { once: true });

  error.textContent = "";
  error.hidden = true;
  setStatus(root, status, "loading", "Loading verified export");
  try {
    const published = await openExport(httpSource("/export/"), {
      signal: controller.signal,
    });
    const widget = await published
      .scenario(scenarioId)
      .output("raw_counter", "anywidget")
      .load(anywidget<CounterState>(), { signal: controller.signal });
    const candidate = await widget.mount(host, { signal: controller.signal });
    if (controller.signal.aborted) {
      try {
        await candidate.dispose();
      } catch (cause) {
        showError(cause, "Disposal failed");
      }
      return;
    }
    mounted = candidate;

    mounted.model.on("change:count", showCount);
    control.addEventListener("click", addFive);
    controlsBound = true;
    control.disabled = false;
    showCount();
    setStatus(root, status, "ready", "Mounted from /export");
  } catch (cause) {
    if (controller.signal.aborted) return;
    window.removeEventListener("pagehide", onPageHide);
    try {
      await releaseMounted();
    } catch (cleanupCause) {
      showError(cleanupCause, "Disposal failed");
      return;
    }
    showError(cause, "Hydration failed");
  }
}

function requireElement<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (element === null) throw new Error(`Astro AnyWidget example is missing ${selector}.`);
  return element;
}

function setStatus(
  root: HTMLElement,
  status: HTMLElement,
  value: "disposed" | "error" | "loading" | "ready",
  label: string,
): void {
  root.dataset.widgetStatus = value;
  status.textContent = label;
}
