import type { JsonObject, OutputLoader } from "@marimo-team/marimo-export";
import type { EmbedOptions, Result as VegaEmbedResult, VisualizationSpec } from "vega-embed";

const FORMAT_ID = "vegalite.v1";

export type VegaLiteSpec = Readonly<JsonObject>;
export type VegaLiteMountOptions = EmbedOptions;
export type MountedVegaLite = VegaEmbedResult;

export interface VegaLiteChart {
  readonly spec: VegaLiteSpec;
  mount(element: HTMLElement, options?: VegaLiteMountOptions): Promise<MountedVegaLite>;
}

/** Load a Vega-Lite projection and prepare it for browser mounting. */
export function vegaLite(defaults: VegaLiteMountOptions = {}): OutputLoader<VegaLiteChart> {
  const defaultOptions = { ...defaults };
  return {
    formatId: FORMAT_ID,
    async load(output) {
      const value = await output.json();
      if (value === null || typeof value !== "object" || Array.isArray(value)) {
        throw new TypeError("Vega-Lite output must contain a JSON object.");
      }
      const template = value as JsonObject;
      const spec = freezeJson(structuredClone(template));
      return {
        spec,
        async mount(element, options) {
          const { default: embed } = await import("vega-embed");
          return embed(element, structuredClone(template) as unknown as VisualizationSpec, {
            renderer: "canvas",
            ...defaultOptions,
            ...options,
          });
        },
      };
    },
  };
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
