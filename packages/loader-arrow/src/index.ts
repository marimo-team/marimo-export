import { tableFromIPC } from "@uwdata/flechette";
import {
  defineLoader,
  type FormatLoader,
  type FormatLoaderContext,
  type FormatRecord,
  type BlobRef,
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

export interface ArrowFormatHandle {
  record: FormatRecord;
  blob: BlobRef;
  metadata: JsonObject | null;
  url(): string;
  bytes(): Promise<Uint8Array>;
  table(options?: ArrowLoadOptions): Promise<unknown>;
  rows(options?: ArrowLoadOptions): Promise<unknown[]>;
  columns(options?: ArrowLoadOptions): Promise<Record<string, unknown>>;
}

export function arrowLoader(defaults: ArrowLoadOptions = {}): FormatLoader<ArrowFormatHandle> {
  return defineLoader({
    formatId: dataframeArrowFormat,
    load(context: FormatLoaderContext) {
      return createArrowHandle(context, defaults);
    },
  });
}

function createArrowHandle(
  context: FormatLoaderContext,
  defaults: ArrowLoadOptions,
): ArrowFormatHandle {
  const loadTable = async (options?: ArrowLoadOptions): Promise<unknown> => {
    const bytes = await context.entry().bytes();
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    return tableFromIPC(buffer, { ...defaults, ...options });
  };

  return {
    record: context.record,
    blob: context.entry().ref,
    metadata: context.record.metadata,
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
