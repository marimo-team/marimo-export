import {
  defineLoader,
  type ArtifactLoader,
  type ArtifactLoaderContext,
  type ArtifactRecord,
  type BlobRef,
  type JsonObject,
} from "@marimo-team/export-reader";
import type { EmbedOptions, Result as VegaEmbedResult } from "vega-embed";

export const vegaliteFormat = "vegalite.v1";

export type VegaLiteSpec = Record<string, unknown>;
export type VegaLiteRenderOptions = EmbedOptions;
export type VegaLiteRenderResult = VegaEmbedResult;

export interface VegaLiteArtifactHandle {
  artifact: ArtifactRecord;
  blob: BlobRef;
  metadata: JsonObject | null;
  url(): string;
  spec<T extends VegaLiteSpec = VegaLiteSpec>(): Promise<T>;
  render(element: HTMLElement, options?: VegaLiteRenderOptions): Promise<VegaLiteRenderResult>;
}

export function vegaliteLoader(
  defaults: VegaLiteRenderOptions = {},
): ArtifactLoader<VegaLiteArtifactHandle> {
  return defineLoader({
    formats: vegaliteFormat,
    load(context: ArtifactLoaderContext) {
      return createVegaLiteHandle(context, defaults);
    },
  });
}

function createVegaLiteHandle(
  context: ArtifactLoaderContext,
  defaults: VegaLiteRenderOptions,
): VegaLiteArtifactHandle {
  return {
    artifact: context.artifact,
    blob: context.file(),
    metadata: context.artifact.metadata,
    url() {
      return context.url();
    },
    spec<T extends VegaLiteSpec = VegaLiteSpec>() {
      return context.json<T>();
    },
    async render(element, options) {
      const spec = await context.json<VegaLiteSpec>();
      const { default: embed } = await import("vega-embed");
      return embed(element, spec, {
        renderer: "canvas",
        ...defaults,
        ...options,
      });
    },
  };
}
