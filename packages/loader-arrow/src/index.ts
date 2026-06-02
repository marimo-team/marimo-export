import { tableFromIPC } from "@uwdata/flechette";
import {
  type ExportBlob,
  type ExportLoader,
  type ExportLoaderContext,
  type JsonObject,
} from "@marimo-team/export-reader";

export const dataframeArrowFormat = "dataframe.arrow.v1";

export interface ArrowLoadOptions {
  useBigInt?: boolean;
  useBigIntTimestamp?: boolean;
  useDate?: boolean;
  useDecimalInt?: boolean;
  useMap?: boolean;
  useProxy?: boolean;
}

export interface ArrowTable {
  formatId: string;
  blob: ExportBlob;
  metadata: JsonObject | null;
  url(): string;
  bytes(): Promise<Uint8Array>;
  table(options?: ArrowLoadOptions): Promise<unknown>;
  rows(options?: ArrowLoadOptions): Promise<unknown[]>;
  columns(options?: ArrowLoadOptions): Promise<Record<string, unknown>>;
}

export function arrowLoader(defaults: ArrowLoadOptions = {}): ExportLoader<ArrowTable> {
  return {
    formatId: dataframeArrowFormat,
    load(context: ExportLoaderContext) {
      return createArrowHandle(context, defaults);
    },
  };
}

function createArrowHandle(context: ExportLoaderContext, defaults: ArrowLoadOptions): ArrowTable {
  const loadTable = async (options?: ArrowLoadOptions): Promise<unknown> => {
    const bytes = await context.entry().bytes();
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    return tableFromIPC(buffer, { ...defaults, ...options });
  };

  return {
    formatId: context.formatId,
    blob: context.entry().ref,
    metadata: context.metadata,
    url() {
      return context.entry().url();
    },
    bytes() {
      return context.entry().bytes();
    },
    table: loadTable,
    async rows(options) {
      const table = (await loadTable(options)) as {
        toArray(): unknown[];
      };
      return table.toArray();
    },
    async columns(options) {
      const table = (await loadTable(options)) as {
        toColumns(): Record<string, unknown>;
      };
      return table.toColumns();
    },
  };
}
