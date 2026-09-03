import { parsePortableJson } from "@marimo-team/portable-json";

import { preparedAbortReason, throwIfPreparedAborted } from "./cancellation.js";
import { PreparedExportError } from "./errors.js";
import type { PreparedExportManifest } from "./manifest.js";
import { parsePreparedExportManifest } from "./manifest.js";

const MANIFEST_MAX_BYTES = 256 * 1024;

export interface PreparedManifestFetchOptions {
  readonly fetch?: typeof globalThis.fetch;
  readonly signal?: AbortSignal;
}

export const fetchPreparedExportManifest = async (
  url: URL,
  options: PreparedManifestFetchOptions = {},
): Promise<PreparedExportManifest> => {
  throwIfPreparedAborted(options.signal);
  let response: Response;
  try {
    const request: RequestInit = {
      cache: "no-store",
      headers: { Accept: "application/json" },
    };
    if (options.signal !== undefined) request.signal = options.signal;
    response = await (options.fetch ?? globalThis.fetch)(url, request);
  } catch (error) {
    if (options.signal?.aborted) {
      throw preparedAbortReason(options.signal.reason);
    }
    throw new PreparedExportError(
      "manifest_read_failed",
      "The prepared export manifest could not be read.",
      { cause: error },
    );
  }
  if (options.signal?.aborted) {
    await cancelBody(response);
    throwIfPreparedAborted(options.signal);
  }
  if (!response.ok) {
    await cancelBody(response);
    throw new PreparedExportError(
      "manifest_read_failed",
      `The prepared export manifest request failed with ${response.status}.`,
    );
  }
  const bytes = await readManifestBytes(response, options.signal);
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return parsePreparedExportManifest(parsePortableJson(text));
  } catch (error) {
    if (error instanceof PreparedExportError) {
      throw error;
    }
    throw new PreparedExportError(
      "manifest_invalid",
      "The prepared export manifest is not valid portable JSON.",
      { cause: error },
    );
  }
};

const readManifestBytes = async (
  response: Response,
  signal: AbortSignal | undefined,
): Promise<Uint8Array> => {
  if (signal?.aborted) {
    await cancelBody(response);
    throwIfPreparedAborted(signal);
  }
  const length = response.headers.get("Content-Length");
  if (length !== null && /^\d+$/u.test(length) && Number(length) > MANIFEST_MAX_BYTES) {
    await cancelBody(response);
    throw manifestTooLarge();
  }
  const reader = response.body?.getReader();
  if (reader === undefined) {
    return new Uint8Array();
  }
  const chunks: Uint8Array[] = [];
  let size = 0;
  const abort = () => {
    void reader.cancel(signal?.reason).catch(() => {});
  };
  signal?.addEventListener("abort", abort, { once: true });
  try {
    if (signal?.aborted) {
      await reader.cancel(signal.reason);
      throwIfPreparedAborted(signal);
    }
    while (true) {
      throwIfPreparedAborted(signal);
      // Manifest reads stay sequential to enforce the byte bound before allocation.
      // oxlint-disable-next-line no-await-in-loop
      const chunk = await reader.read();
      throwIfPreparedAborted(signal);
      if (chunk.done) {
        break;
      }
      size += chunk.value.byteLength;
      if (size > MANIFEST_MAX_BYTES) {
        // Finish transport cleanup before reporting the byte-limit failure.
        // oxlint-disable-next-line no-await-in-loop
        await reader.cancel();
        throw manifestTooLarge();
      }
      chunks.push(chunk.value);
    }
  } catch (error) {
    if (signal?.aborted) {
      throw preparedAbortReason(signal.reason);
    }
    if (error instanceof PreparedExportError) {
      throw error;
    }
    throw new PreparedExportError(
      "manifest_read_failed",
      "The prepared export manifest body could not be read.",
      { cause: error },
    );
  } finally {
    signal?.removeEventListener("abort", abort);
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
};

const cancelBody = async (response: Response): Promise<void> => {
  try {
    await response.body?.cancel();
  } catch {
    // The request failure remains authoritative when transport cleanup also fails.
  }
};

const manifestTooLarge = (): PreparedExportError =>
  new PreparedExportError(
    "manifest_read_failed",
    `The prepared export manifest exceeds ${MANIFEST_MAX_BYTES} bytes.`,
  );
