import { defineOutputLoader } from "@marimo-team/marimo-export";
import type { OutputLoader } from "@marimo-team/marimo-export";
import { CompressionType, setCompressionCodec, tableFromIPC } from "@uwdata/flechette";
import type { ExtractionOptions, Table } from "@uwdata/flechette";
import { compress, decompress } from "lz4js";

const MAX_DECOMPRESSED_BUFFER_BYTES = 512 * 1024 * 1024;

export interface ArrowTableLoaderOptions {
  readonly extraction?: ExtractionOptions;
}

/** Decode a verified Arrow IPC file as a Flechette table. */
export function arrowTableLoader(
  options: ArrowTableLoaderOptions = {},
): OutputLoader<"apache.arrow.file.v1", Table> {
  const extraction: ExtractionOptions = {
    useBigInt: true,
    ...options.extraction,
  };
  return defineOutputLoader({
    codec: "apache.arrow.file.v1",
    accepts: (_descriptor, mediaType) => mediaType.essence === "application/vnd.apache.arrow.file",
    load({ payload, signal }) {
      signal?.throwIfAborted();
      registerLz4();
      const table = tableFromIPC(payload, extraction);
      signal?.throwIfAborted();
      return table;
    },
  });
}

function registerLz4(): void {
  setCompressionCodec(CompressionType.LZ4_FRAME, {
    decode(bytes, uncompressedLength) {
      if (
        !Number.isSafeInteger(uncompressedLength) ||
        uncompressedLength < 0 ||
        uncompressedLength > MAX_DECOMPRESSED_BUFFER_BYTES
      ) {
        throw new TypeError("Arrow LZ4 buffer exceeds the decompression limit.");
      }
      const result = decompress(bytes, uncompressedLength);
      if (result.byteLength !== uncompressedLength) {
        throw new TypeError("Arrow LZ4 buffer length does not match its declaration.");
      }
      return result;
    },
    encode(bytes) {
      return compress(bytes);
    },
  });
}
