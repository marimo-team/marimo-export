import { parquetReadObjects } from "hyparquet";
import type { Compressors, ParquetReadOptions } from "hyparquet";
import { defineBlobAssetLoader } from "@marimo-team/marimo-export";
import type { BlobAssetLoader } from "@marimo-team/marimo-export";

export interface ParquetRowsLoaderOptions extends Omit<
  ParquetReadOptions,
  "file" | "onComplete" | "rowFormat"
> {
  readonly compressors?: Compressors;
}

export type ParquetValue =
  | null
  | boolean
  | number
  | bigint
  | string
  | Date
  | Uint8Array
  | readonly ParquetValue[]
  | ParquetRow;

export interface ParquetRow {
  readonly [column: string]: ParquetValue;
}

export type ParquetObjectReader = (
  options: Omit<ParquetReadOptions, "onComplete">,
) => Promise<ParquetRow[]>;

/** Read a verified Parquet BlobAsset into row objects. */
export function parquetRowsLoader(
  options: ParquetRowsLoaderOptions = {},
): BlobAssetLoader<readonly ParquetRow[]> {
  return parquetRowsLoaderWith(parquetReadObjects, options);
}

/** @internal */
export function parquetRowsLoaderWith(
  readObjects: ParquetObjectReader,
  options: ParquetRowsLoaderOptions = {},
): BlobAssetLoader<readonly ParquetRow[]> {
  const defaults = { ...options };
  return defineBlobAssetLoader({
    mediaTypes: ["application/vnd.apache.parquet", "application/x-parquet"],
    async load({ payload, signal }) {
      signal?.throwIfAborted();
      const data = payload.data.slice();
      const task = readObjects({
        ...defaults,
        file: data.buffer,
      });
      const rows = await raceAbort(task, signal);
      signal?.throwIfAborted();
      return Object.freeze(rows);
    },
  });
}

async function raceAbort<T>(task: Promise<T>, signal: AbortSignal | undefined): Promise<T> {
  if (signal === undefined) return task;
  signal.throwIfAborted();
  let onAbort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    onAbort = () => reject(abortReason(signal));
    signal.addEventListener("abort", onAbort, { once: true });
  });
  try {
    return await Promise.race([task, aborted]);
  } finally {
    if (onAbort !== undefined) signal.removeEventListener("abort", onAbort);
  }
}

function abortReason(signal: AbortSignal): Error {
  return signal.reason instanceof Error
    ? signal.reason
    : Object.assign(new Error("Parquet load was cancelled."), { name: "AbortError" });
}
