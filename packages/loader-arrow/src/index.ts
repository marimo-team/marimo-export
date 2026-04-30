import { tableFromIPC } from "@uwdata/flechette";
import {
  defineLoader,
  type ArtifactLoader,
  type ArtifactLoaderContext,
  type ArtifactRecord,
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

export interface ArrowArtifactHandle {
  artifact: ArtifactRecord;
  blob: BlobRef;
  metadata: JsonObject | null;
  url(): string;
  bytes(): Promise<Uint8Array>;
  table(options?: ArrowLoadOptions): Promise<unknown>;
  rows(options?: ArrowLoadOptions): Promise<unknown[]>;
  columns(options?: ArrowLoadOptions): Promise<Record<string, unknown>>;
}

export function arrowLoader(defaults: ArrowLoadOptions = {}): ArtifactLoader<ArrowArtifactHandle> {
  return defineLoader({
    formats: dataframeArrowFormat,
    load(context: ArtifactLoaderContext) {
      return createArrowHandle(context, defaults);
    },
  });
}

function createArrowHandle(
  context: ArtifactLoaderContext,
  defaults: ArrowLoadOptions,
): ArrowArtifactHandle {
  const loadTable = async (options?: ArrowLoadOptions): Promise<unknown> => {
    const bytes = await context.bytes();
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    return tableFromIPC(buffer, { ...defaults, ...options });
  };

  return {
    artifact: context.artifact,
    blob: context.file(),
    metadata: context.artifact.metadata,
    url() {
      return context.url();
    },
    bytes() {
      return context.bytes();
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
