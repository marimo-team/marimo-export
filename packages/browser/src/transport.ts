import { PublicationError } from "./types.js";

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
        throw new TypeError("A string publication base must be absolute outside a document.", {
          cause: error,
        });
      }
    } else {
      url = new URL(base, documentBase);
    }
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new TypeError("Publication base must use HTTP or HTTPS.");
  }
  if (url.username !== "" || url.password !== "") {
    throw new TypeError("Publication base must not contain user information.");
  }
  if (url.search !== "" || url.hash !== "") {
    throw new TypeError("Publication base must not contain a query or fragment.");
  }
  if (!url.pathname.endsWith("/")) url.pathname += "/";
  return url;
}

export async function fetchBytes(
  fetcher: typeof globalThis.fetch,
  url: URL,
  path: string,
  options: {
    readonly maxBytes: number;
    readonly signal?: AbortSignal;
    readonly expectedBytes?: number;
  },
): Promise<Uint8Array> {
  throwIfAborted(options.signal);
  let response: Response;
  try {
    response = await fetcher(url, options.signal === undefined ? {} : { signal: options.signal });
  } catch (error) {
    throwReadError(error, options.signal, path);
  }
  if (!response.ok) {
    cancelBody(response, new Error("Unsuccessful response"));
    throw new PublicationError(
      "read_failed",
      `Publication object ${quotePath(path)} was not found.`,
      {
        details: { path: boundedPath(path), status: response.status },
      },
    );
  }

  const declared = decodedContentLength(response);
  if (declared !== undefined) {
    if (declared > options.maxBytes) {
      cancelBody(response, new Error("Response exceeds byte limit"));
      throw limitError(path, options.maxBytes, declared);
    }
    if (options.expectedBytes !== undefined && declared !== options.expectedBytes) {
      cancelBody(response, new Error("Response size differs from descriptor"));
      throw new PublicationError(
        "integrity_failed",
        `Publication object ${quotePath(path)} has an unexpected declared size.`,
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
      throw new PublicationError(
        "integrity_failed",
        `Publication object ${quotePath(path)} has an unexpected size.`,
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
    if (error instanceof PublicationError) throw error;
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
    const bytes = new Uint8Array(await response.arrayBuffer());
    throwIfAborted(signal);
    if (bytes.byteLength > maxBytes) throw limitError(path, maxBytes, bytes.byteLength);
    return bytes;
  }
  const reader = response.body.getReader();
  let buffer = new Uint8Array(Math.min(64 * 1024, maxBytes));
  let size = 0;
  try {
    while (true) {
      throwIfAborted(signal);
      // Response chunks must remain ordered.
      // oxlint-disable-next-line no-await-in-loop
      const next = await reader.read();
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
    throw error;
  } finally {
    reader.releaseLock();
  }
  throwIfAborted(signal);
  return buffer.slice(0, size);
}

function decodedContentLength(response: Response): number | undefined {
  const encoding = response.headers.get("Content-Encoding");
  if (encoding !== null && encoding.trim().toLowerCase() !== "identity") return undefined;
  const header = response.headers.get("Content-Length");
  if (header === null || !/^\d+$/u.test(header)) return undefined;
  const value = Number(header);
  return Number.isSafeInteger(value) ? value : undefined;
}

function limitError(path: string, maxBytes: number, observed: number): PublicationError {
  return new PublicationError(
    "read_limit_exceeded",
    `Publication object ${quotePath(path)} exceeds its byte limit.`,
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
    throw new PublicationError("abort", "Publication operation was aborted.", {
      cause: signal.reason,
    });
  }
}

function throwReadError(error: unknown, signal: AbortSignal | undefined, path: string): never {
  if (signal?.aborted || (error instanceof DOMException && error.name === "AbortError")) {
    throw new PublicationError("abort", "Publication operation was aborted.", { cause: error });
  }
  throw new PublicationError(
    "read_failed",
    `Failed to read publication object ${quotePath(path)}.`,
    {
      cause: error,
      details: { path: boundedPath(path) },
    },
  );
}

function cancelBody(response: Response, reason: unknown): void {
  if (response.body !== null) void response.body.cancel(reason).catch(() => undefined);
}

function boundedPath(path: string): string {
  return path.length <= MAX_DIAGNOSTIC_PATH ? path : `${path.slice(0, MAX_DIAGNOSTIC_PATH - 3)}...`;
}

function quotePath(path: string): string {
  return JSON.stringify(boundedPath(path));
}
