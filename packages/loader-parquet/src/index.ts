import {
  defineLoader,
  type FormatLoader,
  type FormatLoaderContext,
  type FormatRecord,
  type BlobRef,
  type JsonObject,
} from "@marimo-team/export-reader";
import { parquetMetadataAsync, parquetReadObjects, parquetSchema } from "hyparquet";
import type { AsyncBuffer } from "hyparquet";

export const dataframeParquetFormat = "dataframe.parquet.v1";

export interface ParquetReadOptions {
  columns?: string[];
  rowStart?: number;
  rowEnd?: number;
  requestInit?: RequestInit;
}

export interface ParquetMetadata {
  rows: number;
  columns: string[];
  raw: unknown;
}

export interface ParquetFormatHandle {
  record: FormatRecord;
  blob: BlobRef;
  metadata: JsonObject | null;
  url(): string;
  readMetadata(options?: Pick<ParquetReadOptions, "requestInit">): Promise<ParquetMetadata>;
  readRows(options?: ParquetReadOptions): Promise<Record<string, unknown>[]>;
}

export function parquetLoader(
  defaults: ParquetReadOptions = {},
): FormatLoader<ParquetFormatHandle> {
  return defineLoader({
    formatId: dataframeParquetFormat,
    load(context: FormatLoaderContext) {
      return createDataframeHandle(context, defaults);
    },
  });
}

function createDataframeHandle(
  context: FormatLoaderContext,
  defaults: ParquetReadOptions,
): ParquetFormatHandle {
  return {
    record: context.record,
    blob: context.entry().ref,
    metadata: context.record.metadata,
    url() {
      return context.entry().url();
    },
    async readMetadata(options) {
      const file = await parquetFile(context, options?.requestInit ?? defaults.requestInit);
      const metadata = await parquetMetadataAsync(file);
      const schema = parquetSchema(metadata);
      return {
        rows: Number(metadata.num_rows),
        columns: schema.children.map((child) => child.element.name),
        raw: metadata,
      };
    },
    async readRows(options) {
      const merged = { ...defaults, ...options };
      const file = await parquetFile(context, merged.requestInit);
      const readOptions: {
        file: Awaited<ReturnType<typeof parquetFile>>;
        columns?: string[];
        rowStart?: number;
        rowEnd?: number;
      } = { file };
      if (merged.columns) {
        readOptions.columns = merged.columns;
      }
      if (merged.rowStart !== undefined) {
        readOptions.rowStart = merged.rowStart;
      }
      if (merged.rowEnd !== undefined) {
        readOptions.rowEnd = merged.rowEnd;
      }
      return (await parquetReadObjects(readOptions)) as Record<string, unknown>[];
    },
  };
}

async function parquetFile(context: FormatLoaderContext, requestInit?: RequestInit) {
  const bytes = requestInit
    ? new Uint8Array(await (await context.entry().fetch(requestInit)).arrayBuffer())
    : await context.entry().bytes();
  return asyncBufferFromBytes(bytes);
}

function asyncBufferFromBytes(bytes: Uint8Array): AsyncBuffer {
  return {
    byteLength: bytes.byteLength,
    slice(start, end) {
      return arrayBuffer(bytes.slice(start, end));
    },
  };
}

function arrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}
