import {
  defineLoader,
  type ArtifactLoader,
  type ArtifactLoaderContext,
  type ArtifactRecord,
  type BlobRef,
  type JsonObject,
} from "@marimo-team/export-reader";
import {
  asyncBufferFromUrl,
  parquetMetadataAsync,
  parquetReadObjects,
  parquetSchema,
} from "hyparquet";

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

export interface ParquetArtifactHandle {
  artifact: ArtifactRecord;
  blob: BlobRef;
  metadata: JsonObject | null;
  url(): string;
  readMetadata(options?: Pick<ParquetReadOptions, "requestInit">): Promise<ParquetMetadata>;
  readRows(options?: ParquetReadOptions): Promise<Record<string, unknown>[]>;
}

export function dataframeLoader(
  defaults: ParquetReadOptions = {},
): ArtifactLoader<ParquetArtifactHandle> {
  return defineLoader({
    formats: dataframeParquetFormat,
    load(context: ArtifactLoaderContext) {
      return createDataframeHandle(context, defaults);
    },
  });
}

export const parquetLoader = dataframeLoader;

function createDataframeHandle(
  context: ArtifactLoaderContext,
  defaults: ParquetReadOptions,
): ParquetArtifactHandle {
  return {
    artifact: context.artifact,
    blob: context.file(),
    metadata: context.artifact.metadata,
    url() {
      return context.url();
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

async function parquetFile(context: ArtifactLoaderContext, requestInit?: RequestInit) {
  const blob = context.file();
  const options = {
    url: context.url(),
    byteLength: blob.size,
  };
  return requestInit
    ? asyncBufferFromUrl({ ...options, requestInit })
    : asyncBufferFromUrl(options);
}
