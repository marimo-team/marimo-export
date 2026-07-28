import { defineBlobAssetLoader } from "@marimo-team/marimo-export";
import type {
  BlobAssetLoadInput,
  BlobAssetLoader,
  JsonObject,
  MountedView,
} from "@marimo-team/marimo-export";
import type { EmbedOptions, Result as VegaEmbedResult, VisualizationSpec } from "vega-embed";

const MEDIA_TYPE = /^application\/vnd\.vegalite\.v[1-9]\d*\+json$/u;
const OWNED_CLASSES = ["vega-embed", "has-actions"] as const;

export type VegaLiteSpec = Readonly<JsonObject>;
export type VegaLiteMountOptions = EmbedOptions & { readonly signal?: AbortSignal };

export interface MountedVegaLite extends MountedView {
  readonly result: VegaEmbedResult;
}

export interface VegaLiteChart {
  readonly spec: VegaLiteSpec;
  mount(element: HTMLElement, options?: VegaLiteMountOptions): Promise<MountedVegaLite>;
}

/** Load a Vega-Lite projection and prepare it for browser mounting. */
export function vegaLiteLoader(defaults: EmbedOptions = {}): BlobAssetLoader<VegaLiteChart> {
  const defaultOptions = { ...defaults };
  return defineBlobAssetLoader({
    mediaTypes: (mediaType) => MEDIA_TYPE.test(mediaType.essence),
    load(input) {
      return loadChart(input, defaultOptions);
    },
  });
}

async function loadChart(
  input: BlobAssetLoadInput,
  defaults: VegaLiteMountOptions,
): Promise<VegaLiteChart> {
  input.signal?.throwIfAborted();
  const value: unknown = JSON.parse(
    new TextDecoder("utf-8", { fatal: true }).decode(input.payload.data),
  );
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("Vega-Lite output must contain a JSON object.");
  }
  input.signal?.throwIfAborted();
  const template = value as JsonObject;
  const spec = freezeJson(structuredClone(template));
  return Object.freeze({
    spec,
    async mount(element: HTMLElement, options: VegaLiteMountOptions = {}) {
      const { signal, ...embedOptions } = options;
      signal?.throwIfAborted();
      const container = element.ownerDocument.createElement("div");
      element.replaceChildren(container);
      let embedTask: Promise<VegaEmbedResult> | undefined;
      let result: VegaEmbedResult;
      try {
        const { default: embed } = await raceAbort(
          import("vega-embed"),
          signal,
          "Vega-Lite mount was cancelled.",
        );
        signal?.throwIfAborted();
        embedTask = embed(container, structuredClone(template) as unknown as VisualizationSpec, {
          renderer: "canvas",
          ...defaults,
          ...embedOptions,
        });
        result = await raceAbort(embedTask, signal, "Vega-Lite mount was cancelled.");
        signal?.throwIfAborted();
      } catch (error) {
        if (embedTask !== undefined) {
          void embedTask.then(
            (lateResult) => finalizeLate(lateResult, container),
            () => undefined,
          );
        }
        clearMount(container);
        throw error;
      }
      let disposed = false;
      return Object.freeze({
        result,
        dispose() {
          if (disposed) return;
          try {
            result.finalize();
            disposed = true;
          } finally {
            clearMount(container);
          }
        },
      });
    },
  });
}

async function raceAbort<T>(
  task: Promise<T>,
  signal: AbortSignal | undefined,
  message: string,
): Promise<T> {
  if (signal === undefined) return task;
  signal.throwIfAborted();
  let onAbort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    onAbort = () => reject(abortReason(signal, message));
    signal.addEventListener("abort", onAbort, { once: true });
  });
  try {
    return await Promise.race([task, aborted]);
  } finally {
    if (onAbort !== undefined) signal.removeEventListener("abort", onAbort);
  }
}

function abortReason(signal: AbortSignal, message: string): Error {
  return signal.reason instanceof Error
    ? signal.reason
    : Object.assign(new Error(message), { name: "AbortError" });
}

function finalizeLate(result: VegaEmbedResult, container: HTMLElement): void {
  try {
    result.finalize();
  } catch (error) {
    console.error("Vega-Lite finalized after its mount was cancelled.", error);
  } finally {
    clearMount(container);
  }
}

function clearMount(container: HTMLElement): void {
  container.replaceChildren();
  container.classList.remove(...OWNED_CLASSES);
  container.remove();
}

function freezeJson<T extends JsonObject>(value: T): Readonly<T> {
  for (const child of Object.values(value)) {
    if (child !== null && typeof child === "object") {
      if (Array.isArray(child)) {
        freezeArray(child);
      } else {
        freezeJson(child as JsonObject);
      }
    }
  }
  return Object.freeze(value);
}

function freezeArray(value: readonly unknown[]): void {
  for (const child of value) {
    if (child !== null && typeof child === "object") {
      if (Array.isArray(child)) {
        freezeArray(child);
      } else {
        freezeJson(child as JsonObject);
      }
    }
  }
  Object.freeze(value);
}
