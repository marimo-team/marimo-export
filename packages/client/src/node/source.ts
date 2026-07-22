import { realpath } from "node:fs/promises";
import { resolve, sep } from "node:path";

import { sourcePath } from "../source.js";
import type { ExportSource, ReadOptions } from "../types.js";
import { MarimoExportError } from "../types.js";
import { LocalFileTooLargeError, readRegularFile } from "./safe-file.js";

const DEFAULT_LOCAL_MAX_BYTES = 16 * 1024 * 1024;

export function directorySource(rootPath: string): ExportSource {
  const root = resolve(rootPath);
  return Object.freeze({
    async read(path: string, readOptions: ReadOptions = {}) {
      const destination = exportPath(root, path);
      const maxBytes = readLimit(readOptions.maxBytes) ?? DEFAULT_LOCAL_MAX_BYTES;
      try {
        readOptions.signal?.throwIfAborted();
        await assertInsideRealRoot(root, destination);
        return await readRegularFile(destination, maxBytes, readOptions.signal);
      } catch (error) {
        if (readOptions.signal?.aborted === true) throw readOptions.signal.reason;
        if (error instanceof MarimoExportError) throw error;
        if (error instanceof LocalFileTooLargeError) {
          throw new MarimoExportError(
            "output_too_large",
            `Export object ${JSON.stringify(path)} exceeds the ${error.maxBytes} byte read limit.`,
            {
              details: {
                path,
                maxBytes: error.maxBytes,
                observedBytes:
                  error.size <= BigInt(Number.MAX_SAFE_INTEGER)
                    ? Number(error.size)
                    : error.size.toString(),
              },
            },
          );
        }
        throw new MarimoExportError(
          "source_read_failed",
          `Failed to read export object ${JSON.stringify(path)} from ${root}.`,
          { cause: error },
        );
      }
    },
  });
}

export function exportPath(rootPath: string, path: string): string {
  const portable = sourcePath(path);
  const root = resolve(rootPath);
  const destination = resolve(root, ...portable.split("/"));
  if (destination !== root && !destination.startsWith(`${root}${sep}`)) {
    throw new MarimoExportError(
      "source_read_failed",
      `Export path ${JSON.stringify(path)} escapes ${root}.`,
    );
  }
  return destination;
}

async function assertInsideRealRoot(root: string, path: string): Promise<void> {
  const [realRoot, realPath] = await Promise.all([realpath(root), realpath(path)]);
  if (realPath !== realRoot && !realPath.startsWith(`${realRoot}${sep}`)) {
    throw new MarimoExportError(
      "source_read_failed",
      `Export object ${JSON.stringify(path)} resolves outside ${root}.`,
    );
  }
}

function readLimit(input: number | undefined): number | undefined {
  if (input === undefined) return undefined;
  if (!Number.isSafeInteger(input) || input < 0) {
    throw new TypeError("maxBytes must be a non-negative safe integer.");
  }
  return input;
}
