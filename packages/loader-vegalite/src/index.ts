import type vegaEmbed from "vega-embed";
import type { EmbedOptions, Result as VegaEmbedResult, VisualizationSpec } from "vega-embed";
import { defineBlobAssetLoader } from "@marimo-team/marimo-export";
import { portableJsonObject } from "@marimo-team/portable-json";
import type {
  BlobAssetLoadInput,
  BlobAssetLoader,
  JsonObject,
  MountedView,
} from "@marimo-team/marimo-export";

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

export type VegaEmbed = typeof vegaEmbed;
export type VegaEmbedLoader = () => Promise<VegaEmbed>;

/** Load an exported Vega-Lite value and prepare it for browser mounting. */
export function vegaLiteLoader(defaults: EmbedOptions = {}): BlobAssetLoader<VegaLiteChart> {
  return vegaLiteLoaderWith(async () => (await import("vega-embed")).default, defaults);
}

/** @internal */
export function vegaLiteLoaderWith(
  loadEmbed: VegaEmbedLoader,
  defaults: EmbedOptions = {},
): BlobAssetLoader<VegaLiteChart> {
  const defaultOptions = { ...defaults };
  return defineBlobAssetLoader({
    mediaTypes: (mediaType) => MEDIA_TYPE.test(mediaType.essence),
    load(input) {
      return loadChart(input, defaultOptions, loadEmbed);
    },
  });
}

async function loadChart(
  input: BlobAssetLoadInput,
  defaults: VegaLiteMountOptions,
  loadEmbed: VegaEmbedLoader,
): Promise<VegaLiteChart> {
  input.signal?.throwIfAborted();
  const template = portableJsonObject(
    JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(input.payload.data)),
    "Vega-Lite output",
  );
  input.signal?.throwIfAborted();
  const spec = template;
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
        const embed = await raceAbort(loadEmbed(), signal, "Vega-Lite mount was cancelled.");
        signal?.throwIfAborted();
        embedTask = embed(container, visualizationSpec(template), {
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
          disposed = true;
          try {
            result.finalize();
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

function visualizationSpec(value: VegaLiteSpec): VisualizationSpec {
  // SAFETY: portableJsonObject validated the complete Vega-Lite JSON value before embedding.
  return structuredClone(value) as VisualizationSpec;
}
