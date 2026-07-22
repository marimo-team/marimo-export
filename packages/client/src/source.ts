import { assertPortablePath } from "./schema.js";
import type { ExportSource, ReadOptions } from "./types.js";
import { MarimoExportError } from "./types.js";

export interface HttpSourceOptions {
  readonly base?: string | URL;
  readonly fetch?: typeof fetch;
  readonly headers?: Readonly<Record<string, string>>;
}

export function httpSource(root: string | URL, options: HttpSourceOptions = {}): ExportSource {
  const rootUrl = directoryUrl(root, options.base);
  const fetchOption = options.fetch;
  const headers = options.headers === undefined ? undefined : Object.freeze({ ...options.headers });
  return Object.freeze({
    async read(path: string, readOptions: ReadOptions = {}) {
      const fetchImpl = fetchOption ?? globalThis.fetch;
      if (fetchImpl === undefined) {
        throw new MarimoExportError("source_read_failed", "HTTP export reading requires fetch.");
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
          throw readOptions.signal.reason;
        }
        if (response.redirected) {
          const error = new MarimoExportError(
            "source_read_failed",
            `HTTP export reading must not follow redirects for ${JSON.stringify(portablePath)}.`,
          );
          cancelResponseBody(response, error);
          throw error;
        }
        if (!response.ok) {
          const error = new MarimoExportError(
            "source_read_failed",
            `Failed to read ${JSON.stringify(portablePath)}: ${response.status} ${response.statusText}.`,
          );
          cancelResponseBody(response, error);
          throw error;
        }
        return await readResponse(response, portablePath, maxBytes, readOptions.signal);
      } catch (error) {
        if (readOptions.signal?.aborted === true || error instanceof MarimoExportError) throw error;
        throw new MarimoExportError(
          "source_read_failed",
          `Failed to read ${JSON.stringify(portablePath)} from ${rootUrl.origin}.`,
          { cause: error },
        );
      }
    },
  });
}

export type MemorySourceInput =
  | ReadonlyMap<string, Uint8Array | string>
  | Readonly<Record<string, Uint8Array | string>>;

export function memorySource(input: MemorySourceInput): ExportSource {
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
        throw new MarimoExportError(
          "source_read_failed",
          `Export object ${JSON.stringify(portablePath)} is missing.`,
        );
      }
      enforceLimit(value.byteLength, readLimit(options.maxBytes), portablePath);
      options.signal?.throwIfAborted();
      return new Uint8Array(value);
    },
  });
}

export function sourcePath(path: string): string {
  try {
    return assertPortablePath(path);
  } catch (error) {
    throw new MarimoExportError(
      "source_read_failed",
      `Export object path ${JSON.stringify(path)} must be a portable relative path.`,
      { cause: error },
    );
  }
}

function isReadonlyMap(
  input: MemorySourceInput,
): input is ReadonlyMap<string, Uint8Array | string> {
  return typeof (input as { readonly entries?: unknown }).entries === "function";
}

function directoryUrl(value: string | URL, explicitBase?: string | URL): URL {
  let url: URL;
  if (value instanceof URL) {
    url = new URL(value);
  } else {
    try {
      url = new URL(value);
    } catch {
      const base = explicitBase ?? browserLocation();
      if (base === undefined) {
        throw new TypeError("Relative HTTP export roots require options.base outside a browser.");
      }
      let baseUrl: URL;
      try {
        baseUrl = new URL(base);
      } catch (error) {
        throw new TypeError("HTTP export source base must be an absolute URL.", { cause: error });
      }
      assertHttpBase(baseUrl, "HTTP export source base");
      url = new URL(value, baseUrl);
    }
  }
  assertHttpUrl(url, "HTTP export root");
  return new URL(url.pathname.endsWith("/") ? url.href : `${url.href}/`);
}

function assertHttpBase(url: URL, label: string): void {
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new TypeError(`${label} must use HTTP or HTTPS.`);
  }
  if (url.username.length > 0 || url.password.length > 0) {
    throw new TypeError(`${label} must not contain embedded credentials.`);
  }
}

function assertHttpUrl(url: URL, label: string): void {
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new TypeError(`${label} must use HTTP or HTTPS.`);
  }
  if (url.username.length > 0 || url.password.length > 0) {
    throw new TypeError(`${label} must not contain embedded credentials.`);
  }
  if (url.search.length > 0 || url.hash.length > 0) {
    throw new TypeError(`${label} must not contain a query or fragment.`);
  }
}

function browserLocation(): string | undefined {
  return typeof globalThis.location?.href === "string" ? globalThis.location.href : undefined;
}

async function readResponse(
  response: Response,
  path: string,
  maxBytes: number | undefined,
  signal: AbortSignal | undefined,
): Promise<Uint8Array> {
  if (maxBytes === undefined) {
    const bytes = new Uint8Array(await response.arrayBuffer());
    signal?.throwIfAborted();
    return bytes;
  }
  const declared = response.headers.get("Content-Length");
  if (declared !== null && /^\d+$/.test(declared)) {
    try {
      enforceLimit(Number(declared), maxBytes, path);
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

  const chunks: Uint8Array[] = [];
  const reader = response.body.getReader();
  let size = 0;
  try {
    while (true) {
      signal?.throwIfAborted();
      // oxlint-disable-next-line no-await-in-loop -- streamed bytes must stay in response order.
      const next = await reader.read();
      signal?.throwIfAborted();
      if (next.done) break;
      const chunk = new Uint8Array(next.value);
      size += chunk.byteLength;
      enforceLimit(size, maxBytes, path);
      chunks.push(chunk);
    }
  } catch (error) {
    try {
      void reader.cancel(error).catch(() => undefined);
    } catch {
      // The read failure remains authoritative when cancellation is unavailable.
    }
    throw error;
  } finally {
    reader.releaseLock();
  }

  signal?.throwIfAborted();
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  signal?.throwIfAborted();
  return bytes;
}

function cancelResponseBody(response: Response, reason: unknown): void {
  if (response.body === null) return;
  try {
    void response.body.cancel(reason).catch(() => undefined);
  } catch {
    // Rejection remains authoritative when a custom response cannot be cancelled.
  }
}

function readLimit(input: number | undefined): number | undefined {
  if (input === undefined) return undefined;
  if (!Number.isSafeInteger(input) || input < 0) {
    throw new TypeError("maxBytes must be a non-negative safe integer.");
  }
  return input;
}

function enforceLimit(size: number, maxBytes: number | undefined, path: string): void {
  if (maxBytes !== undefined && size > maxBytes) {
    throw new MarimoExportError(
      "output_too_large",
      `Export object ${JSON.stringify(path)} exceeds the ${maxBytes} byte read limit.`,
      { details: { path, maxBytes, observedBytes: size } },
    );
  }
}
