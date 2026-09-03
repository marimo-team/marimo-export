import { isAbortError } from "./abort.js";
import { isNotebookExportError, NotebookExportError } from "./types.js";

const MAX_DIAGNOSTIC_PATH = 256;

export function normalizeBase(base: string | URL): URL {
  let url: URL;
  if (base instanceof URL) {
    url = new URL(base.href);
  } else {
    const documentBase = globalThis.document?.baseURI;
    if (documentBase === undefined) {
      try {
        url = new URL(base);
      } catch (error) {
        throw new TypeError("A string export base must be absolute outside a document.", {
          cause: error,
        });
      }
    } else {
      url = new URL(base, documentBase);
    }
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new TypeError("Export base URL must use HTTP or HTTPS.");
  }
  if (url.username !== "" || url.password !== "") {
    throw new TypeError("Export base URL must not contain user information.");
  }
  if (url.hash !== "") throw new TypeError("Export base URL must not contain a fragment.");
  if (!url.pathname.endsWith("/")) url.pathname += "/";
  return url;
}

export function resolveExportUrl(base: URL | string, path: string): URL {
  if (path.length === 0 || path.includes("?") || path.includes("#")) {
    throw new TypeError("Export object path must be a non-empty path without a query or fragment.");
  }
  const normalized = base instanceof URL ? base : new URL(base);
  const url = new URL(path, normalized);
  url.search = normalized.search;
  return url;
}

export async function fetchBytes(
  fetcher: typeof globalThis.fetch,
  url: URL,
  path: string,
  options: {
    readonly cache?: RequestCache;
    readonly maxBytes: number;
    readonly signal?: AbortSignal;
    readonly expectedBytes?: number;
  },
): Promise<Uint8Array> {
  throwIfAborted(options.signal);
  let response: Response;
  try {
    const request: RequestInit = {};
    if (options.cache !== undefined) request.cache = options.cache;
    if (options.signal !== undefined) request.signal = options.signal;
    response = await fetcher(url, request);
  } catch (error) {
    throwReadError(error, options.signal, path);
  }
  if (!response.ok) {
    cancelBody(response, new Error("Unsuccessful response"));
    throw new NotebookExportError("read_failed", `Export file ${quotePath(path)} was not found.`, {
      details: { path: boundedPath(path), status: response.status },
    });
  }

  const declared = decodedContentLength(response);
  if (declared !== undefined) {
    if (declared > options.maxBytes) {
      cancelBody(response, new Error("Response exceeds byte limit"));
      throw limitError(path, options.maxBytes, declared);
    }
    if (options.expectedBytes !== undefined && declared !== options.expectedBytes) {
      cancelBody(response, new Error("Response size differs from descriptor"));
      throw new NotebookExportError(
        "integrity_failed",
        `Export file ${quotePath(path)} has an unexpected declared size.`,
        {
          details: {
            path: boundedPath(path),
            expectedSize: options.expectedBytes,
            observedSize: declared,
          },
        },
      );
    }
  }

  try {
    const bytes = await readBody(response, path, options.maxBytes, options.signal);
    if (options.expectedBytes !== undefined && bytes.byteLength !== options.expectedBytes) {
      throw new NotebookExportError(
        "integrity_failed",
        `Export file ${quotePath(path)} has an unexpected size.`,
        {
          details: {
            path: boundedPath(path),
            expectedSize: options.expectedBytes,
            observedSize: bytes.byteLength,
          },
        },
      );
    }
    return bytes;
  } catch (error) {
    if (isNotebookExportError(error)) throw error;
    throwReadError(error, options.signal, path);
  }
}

async function readBody(
  response: Response,
  path: string,
  maxBytes: number,
  signal: AbortSignal | undefined,
): Promise<Uint8Array> {
  if (response.body === null) {
    throw new NotebookExportError(
      "read_failed",
      `Export file ${quotePath(path)} has no readable response body.`,
      { details: { path: boundedPath(path) } },
    );
  }
  const reader = response.body.getReader();
  let buffer = new Uint8Array(Math.min(64 * 1024, maxBytes));
  let size = 0;
  let pendingRead: Promise<ReadableStreamReadResult<Uint8Array>> | undefined;
  let deferredRelease = false;
  try {
    while (true) {
      throwIfAborted(signal);
      // Response chunks must remain ordered.
      pendingRead = reader.read();
      // oxlint-disable-next-line no-await-in-loop
      const next = await waitForRead(pendingRead, reader, signal);
      pendingRead = undefined;
      if (next.done) break;
      const chunk = next.value;
      if (chunk.byteLength > maxBytes - size) {
        throw limitError(path, maxBytes, size + chunk.byteLength);
      }
      const required = size + chunk.byteLength;
      if (required > buffer.byteLength) {
        let capacity = Math.max(buffer.byteLength, 1);
        while (capacity < required) capacity = Math.min(maxBytes, Math.max(required, capacity * 2));
        const grown = new Uint8Array(capacity);
        grown.set(buffer);
        buffer = grown;
      }
      buffer.set(chunk, size);
      size = required;
    }
  } catch (error) {
    void reader.cancel(error).catch(() => undefined);
    if (pendingRead !== undefined) {
      deferredRelease = true;
      void pendingRead
        .finally(() => {
          reader.releaseLock();
        })
        .catch(() => undefined);
    }
    throw error;
  } finally {
    if (!deferredRelease) reader.releaseLock();
  }
  throwIfAborted(signal);
  return buffer.slice(0, size);
}

async function waitForRead(
  read: Promise<ReadableStreamReadResult<Uint8Array>>,
  reader: ReadableStreamDefaultReader<Uint8Array>,
  signal: AbortSignal | undefined,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  if (signal === undefined) return read;
  throwIfAborted(signal);
  let abort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    abort = () => {
      const error = new NotebookExportError("abort", "Export operation was aborted.", {
        cause: signal.reason,
      });
      void reader.cancel(signal.reason).catch(() => undefined);
      reject(error);
    };
    signal.addEventListener("abort", abort, { once: true });
  });
  try {
    return await Promise.race([read, aborted]);
  } finally {
    if (abort !== undefined) signal.removeEventListener("abort", abort);
  }
}

function decodedContentLength(response: Response): number | undefined {
  const encoding = response.headers.get("Content-Encoding");
  if (encoding !== null && encoding.trim().toLowerCase() !== "identity") return undefined;
  const header = response.headers.get("Content-Length");
  if (header === null || !/^\d+$/u.test(header)) return undefined;
  const value = Number(header);
  return Number.isSafeInteger(value) ? value : undefined;
}

function limitError(path: string, maxBytes: number, observed: number): NotebookExportError {
  return new NotebookExportError(
    "read_limit_exceeded",
    `Export file ${quotePath(path)} exceeds its byte limit.`,
    {
      details: {
        path: boundedPath(path),
        maxBytes,
        observedBytes: observed,
      },
    },
  );
}

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) {
    throw new NotebookExportError("abort", "Export operation was aborted.", {
      cause: signal.reason,
    });
  }
}

function throwReadError(cause: unknown, signal: AbortSignal | undefined, path: string): never {
  if (signal?.aborted || isAbortError(cause)) {
    throw new NotebookExportError("abort", "Export operation was aborted.", { cause });
  }
  throw new NotebookExportError("read_failed", `Failed to read export object ${quotePath(path)}.`, {
    cause,
    details: { path: boundedPath(path) },
  });
}

function cancelBody(response: Response, cause: unknown): void {
  if (response.body !== null) void response.body.cancel(cause).catch(() => undefined);
}

function boundedPath(path: string): string {
  return path.length <= MAX_DIAGNOSTIC_PATH ? path : `${path.slice(0, MAX_DIAGNOSTIC_PATH - 3)}...`;
}

function quotePath(path: string): string {
  return JSON.stringify(boundedPath(path));
}
