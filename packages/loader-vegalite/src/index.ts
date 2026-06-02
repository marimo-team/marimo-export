import {
  type ExportBlob,
  type ExportLoader,
  type ExportLoaderContext,
  type JsonObject,
} from "@marimo-team/export-reader";
import type { EmbedOptions, Result as VegaEmbedResult } from "vega-embed";

export const vegaliteFormat = "vegalite.v1";

export type VegaLiteSpec = Record<string, unknown>;
export type VegaLiteRenderOptions = EmbedOptions;
export type VegaLiteRenderResult = VegaEmbedResult;

export interface VegaLiteChart {
  formatId: string;
  blob: ExportBlob;
  metadata: JsonObject | null;
  url(): string;
  spec<T extends VegaLiteSpec = VegaLiteSpec>(): Promise<T>;
  render(element: HTMLElement, options?: VegaLiteRenderOptions): Promise<VegaLiteRenderResult>;
}

export function vegaliteLoader(defaults: VegaLiteRenderOptions = {}): ExportLoader<VegaLiteChart> {
  return {
    formatId: vegaliteFormat,
    load(context: ExportLoaderContext) {
      return createVegaLiteHandle(context, defaults);
    },
  };
}

function createVegaLiteHandle(
  context: ExportLoaderContext,
  defaults: VegaLiteRenderOptions,
): VegaLiteChart {
  return {
    formatId: context.formatId,
    blob: context.entry().ref,
    metadata: context.metadata,
    url() {
      return context.entry().url();
    },
    spec<T extends VegaLiteSpec = VegaLiteSpec>() {
      return context.entry().json<T>();
    },
    async render(element, options) {
      const spec = await context.entry().json<VegaLiteSpec>();
      const { default: embed } = await import("vega-embed");
      return embed(element, spec, {
        renderer: "canvas",
        ...defaults,
        ...options,
      });
    },
  };
}
