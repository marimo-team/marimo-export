import type { BlobRef, JsonValue } from "@marimo-team/export-reader";

export interface AnyWidgetBufferRef {
  path: Array<string | number>;
  data: BlobRef;
}

export interface AnyWidgetAssetRefs {
  module: BlobRef;
  style?: BlobRef | null;
}

export interface AnyWidgetDescriptor {
  schema: "moexport.anywidget.bundle.v1";
  anywidget_id: string;
  state: Record<string, JsonValue | BlobRef>;
  assets: AnyWidgetAssetRefs;
  buffers: AnyWidgetBufferRef[];
}

export type AnyWidgetState = object;
