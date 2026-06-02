import type { ExportBlob, JsonValue } from "@marimo-team/export-reader";

export interface AnyWidgetBufferRef {
  path: Array<string | number>;
  data: ExportBlob;
}

export interface AnyWidgetAssetRefs {
  module: ExportBlob;
  style?: ExportBlob | null;
}

export interface AnyWidgetDescriptor {
  schema: "moexport.anywidget.bundle.v1";
  anywidget_id: string;
  state: Record<string, JsonValue | ExportBlob>;
  assets: AnyWidgetAssetRefs;
  buffers: AnyWidgetBufferRef[];
}

export type AnyWidgetState = object;
