import type { OutputLoader } from "@marimo-team/marimo-export";
import { parquetReadObjects } from "hyparquet";
import type { AsyncBuffer, ParquetReadOptions } from "hyparquet";

const FORMAT_ID = "dataframe.parquet.v1";

export type ParquetOptions = Omit<
  ParquetReadOptions,
  "file" | "onChunk" | "onComplete" | "onPage" | "rowFormat"
>;

/** Decode a Parquet projection into row objects. */
export function parquet<Row extends object = Record<string, unknown>>(
  options: ParquetOptions = {},
): OutputLoader<Row[]> {
  const settings = { ...options };
  return {
    formatId: FORMAT_ID,
    async load(output) {
      const file = asyncBuffer(await output.bytes());
      return (await parquetReadObjects({ ...settings, file })) as Row[];
    },
  };
}

function asyncBuffer(bytes: Uint8Array): AsyncBuffer {
  return {
    byteLength: bytes.byteLength,
    slice(start, end) {
      const slice = bytes.slice(start, end);
      return slice.buffer.slice(
        slice.byteOffset,
        slice.byteOffset + slice.byteLength,
      ) as ArrayBuffer;
    },
  };
}
