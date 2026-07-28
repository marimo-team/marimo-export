import type { ReadOptions } from "./types.js";
import { PublicationError } from "./types.js";
import { isPortablePathComponent } from "./schema.js";

export interface PublicationSource {
  read(path: string, options?: ReadOptions): Promise<Uint8Array>;
}

export interface HttpSourceOptions {
  readonly fetch?: typeof fetch;
  readonly headers?: Readonly<Record<string, string>>;
}

export function httpSource(root: string | URL, options: HttpSourceOptions = {}): PublicationSource {
  const rootUrl = directoryUrl(root);
  const fetchOption = options.fetch;
  const headers =
    options.headers === undefined
      ? undefined
      : Object.freeze(Object.fromEntries(new Headers(options.headers).entries()));
  return Object.freeze({
    async read(path: string, readOptions: ReadOptions = {}) {
      readOptions.signal?.throwIfAborted();
      const fetchImpl = fetchOption ?? globalThis.fetch;
      if (fetchImpl === undefined) {
        throw new PublicationError("read_failed", "Publication reading requires fetch.");
      }
      const portablePath = sourcePath(path);
      const encodedPath = portablePath.split("/").map(encodeURIComponent).join("/");
      const maxBytes = readLimit(readOptions.maxBytes);
      try {
        const response = await fetchImpl(new URL(encodedPath, rootUrl), {
          ...(headers === undefined ? {} : { headers }),
          ...(readOptions.signal === undefined ? {} : { signal: readOptions.signal }),
          redirect: "error",
        });
        if (readOptions.signal?.aborted === true) {
          cancelResponseBody(response, readOptions.signal.reason);
          readOptions.signal.throwIfAborted();
        }
        if (response.redirected) {
          const error = new PublicationError(
            "read_failed",
            `Publication reads cannot follow a redirect for ${JSON.stringify(portablePath)}.`,
          );
          cancelResponseBody(response, error);
          throw error;
        }
        if (!response.ok) {
          const error = new PublicationError(
            "read_failed",
            `Failed to read ${JSON.stringify(portablePath)}: ${response.status} ${response.statusText}.`,
            { details: { path: portablePath, status: response.status } },
          );
          cancelResponseBody(response, error);
          throw error;
        }
        return await readResponse(response, portablePath, maxBytes, readOptions.signal);
      } catch (error) {
        readOptions.signal?.throwIfAborted();
        if (error instanceof PublicationError) throw error;
        throw new PublicationError(
          "read_failed",
          `Failed to read ${JSON.stringify(portablePath)} from ${rootUrl.origin}.`,
          { cause: error, details: { path: portablePath } },
        );
      }
    },
  });
}

export type MemorySourceInput =
  | ReadonlyMap<string, Uint8Array | string>
  | Readonly<Record<string, Uint8Array | string>>;

export function memorySource(input: MemorySourceInput): PublicationSource {
  const sourceEntries = isReadonlyMap(input) ? input.entries() : Object.entries(input);
  const entries = new Map<string, Uint8Array>();
  for (const [path, value] of sourceEntries) {
    const portablePath = sourcePath(path);
    entries.set(
      portablePath,
      typeof value === "string" ? new TextEncoder().encode(value) : new Uint8Array(value),
    );
  }
  return Object.freeze({
    async read(path: string, options: ReadOptions = {}) {
      options.signal?.throwIfAborted();
      const portablePath = sourcePath(path);
      const value = entries.get(portablePath);
      if (value === undefined) {
        throw new PublicationError(
          "read_failed",
          `Publication object ${JSON.stringify(portablePath)} is missing.`,
          { details: { path: portablePath } },
        );
      }
      enforceLimit(value.byteLength, readLimit(options.maxBytes), portablePath);
      options.signal?.throwIfAborted();
      return new Uint8Array(value);
    },
  });
}

export function sourcePath(path: string): string {
  if (
    path.startsWith("/") ||
    path.split("/").some((segment) => !isPortablePathComponent(segment))
  ) {
    throw new PublicationError(
      "read_failed",
      `Publication object path ${JSON.stringify(path)} must be a portable relative POSIX path.`,
      { details: { path } },
    );
  }
  return path;
}

function isReadonlyMap(
  input: MemorySourceInput,
): input is ReadonlyMap<string, Uint8Array | string> {
  return typeof (input as { readonly entries?: unknown }).entries === "function";
}

function directoryUrl(value: string | URL): URL {
  const supplied = value instanceof URL ? value.href : value;
  if (supplied.includes("?") || supplied.includes("#")) {
    throw new TypeError("Publication root must not contain a query or fragment.");
  }
  let url: URL;
  if (value instanceof URL) {
    url = new URL(value);
  } else {
    try {
      url = new URL(value);
    } catch {
      if (typeof globalThis.location?.href !== "string") {
        throw new TypeError("Relative publication roots require a browser location.");
      }
      url = new URL(value, globalThis.location.href);
    }
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new TypeError("Publication root must use HTTP or HTTPS.");
  }
  if (url.username.length > 0 || url.password.length > 0) {
    throw new TypeError("Publication root must not contain embedded credentials.");
  }
  if (url.search.length > 0 || url.hash.length > 0) {
    throw new TypeError("Publication root must not contain a query or fragment.");
  }
  return new URL(url.pathname.endsWith("/") ? url.href : `${url.href}/`);
}

async function readResponse(
  response: Response,
  path: string,
  maxBytes: number | undefined,
  signal: AbortSignal | undefined,
): Promise<Uint8Array> {
  const declared = response.headers.get("Content-Length");
  const contentEncoding = response.headers.get("Content-Encoding");
  const hasDecodedContentLength =
    contentEncoding === null || contentEncoding.trim().toLowerCase() === "identity";
  let declaredSize: number | undefined;
  if (
    maxBytes !== undefined &&
    declared !== null &&
    hasDecodedContentLength &&
    /^\d+$/.test(declared)
  ) {
    try {
      const parsedSize = Number(declared);
      if (!Number.isSafeInteger(parsedSize)) {
        throw new PublicationError(
          "read_limit_exceeded",
          `Publication object ${JSON.stringify(path)} declares a size outside the safe integer range.`,
          { details: { path, maxBytes, declaredBytes: declared } },
        );
      }
      enforceLimit(parsedSize, maxBytes, path);
      declaredSize = parsedSize;
    } catch (error) {
      cancelResponseBody(response, error);
      throw error;
    }
  }
  if (response.body === null) {
    const bytes = new Uint8Array(await response.arrayBuffer());
    signal?.throwIfAborted();
    enforceLimit(bytes.byteLength, maxBytes, path);
    return bytes;
  }

  const reader = response.body.getReader();
  let buffer: Uint8Array<ArrayBuffer> = new Uint8Array(
    declaredSize ?? Math.min(maxBytes ?? 64 * 1024, 64 * 1024),
  );
  let size = 0;
  try {
    while (true) {
      signal?.throwIfAborted();
      // oxlint-disable-next-line no-await-in-loop -- response chunks must remain ordered.
      const next = await readStreamChunk(reader, signal);
      signal?.throwIfAborted();
      if (next.done) break;
      const chunk = new Uint8Array(next.value);
      if (chunk.byteLength === 0) continue;
      if (chunk.byteLength > Number.MAX_SAFE_INTEGER - size) {
        throw new PublicationError(
          "read_limit_exceeded",
          `Publication object ${JSON.stringify(path)} exceeds the safe integer byte range.`,
          { details: { path, maxBytes: maxBytes ?? null } },
        );
      }
      size += chunk.byteLength;
      enforceLimit(size, maxBytes, path);
      buffer = ensureCapacity(buffer, size, maxBytes);
      buffer.set(chunk, size - chunk.byteLength);
    }
  } catch (error) {
    try {
      void reader.cancel(error).catch(() => undefined);
    } catch {
      // The read failure remains authoritative when cancellation is unavailable.
    }
    throw error;
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // A custom stream can retain a pending read after cancellation.
    }
  }

  signal?.throwIfAborted();
  return size === buffer.byteLength ? buffer : buffer.slice(0, size);
}

function ensureCapacity(
  buffer: Uint8Array<ArrayBuffer>,
  required: number,
  maxBytes: number | undefined,
): Uint8Array<ArrayBuffer> {
  if (required <= buffer.byteLength) return buffer;
  let capacity = Math.max(buffer.byteLength, 1);
  while (capacity < required) {
    capacity = Math.max(required, Math.min(Number.MAX_SAFE_INTEGER, capacity * 2));
    if (maxBytes !== undefined) capacity = Math.min(capacity, maxBytes);
  }
  const grown = new Uint8Array(capacity);
  grown.set(buffer);
  return grown;
}

async function readStreamChunk(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  signal: AbortSignal | undefined,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  const read = reader.read();
  if (signal === undefined) return read;
  signal.throwIfAborted();
  let onAbort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    onAbort = () => {
      void reader.cancel(signal.reason).catch(() => undefined);
      reject(signal.reason);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
  try {
    return await Promise.race([read, aborted]);
  } finally {
    if (onAbort !== undefined) signal.removeEventListener("abort", onAbort);
  }
}

function cancelResponseBody(response: Response, reason: unknown): void {
  if (response.body === null) return;
  try {
    void response.body.cancel(reason).catch(() => undefined);
  } catch {
    // A custom response can expose a body whose cancel method throws synchronously.
  }
}

export function readLimit(input: number | undefined): number | undefined {
  if (input === undefined) return undefined;
  if (!Number.isSafeInteger(input) || input < 0) {
    throw new TypeError("Byte limits must be non-negative safe integers.");
  }
  return input;
}

export function enforceLimit(size: number, maxBytes: number | undefined, path: string): void {
  if (maxBytes !== undefined && size > maxBytes) {
    throw new PublicationError(
      "read_limit_exceeded",
      `Publication object ${JSON.stringify(path)} exceeds the ${maxBytes} byte read limit.`,
      { details: { path, maxBytes, observedBytes: size } },
    );
  }
}
