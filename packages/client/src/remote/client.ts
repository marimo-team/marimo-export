import { parseExportRef } from "../schema.js";
import type { ExportRef, JsonObject } from "../types.js";
import { isExportErrorCode, MarimoExportError } from "../types.js";
import { validateExportPlan, type ExportPlan } from "./plan.js";

export const REMOTE_PROTOCOL = "marimo-export.remote.v1" as const;
export const RESPONSE_PREFIX = "__MARIMO_EXPORT_RESPONSE__:" as const;

const MAX_SSE_EVENT_CHARS = 1024 * 1024;
const MAX_RESPONSE_BYTES = 1024 * 1024;

export type RemoteOperation = "describe" | "build" | "stage" | "release";

export interface RemoteTransportOptions {
  server: URL;
  sessionId: string;
  fetch?: typeof fetch;
  headers?: Readonly<Record<string, string>>;
  authToken?: string;
  serverToken?: string;
  timeoutMs?: number;
}

export interface RemoteRequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

export interface RemoteProjectionCapability {
  readonly available: boolean;
  readonly extra: string | null;
}

export interface RemoteDescription {
  readonly protocol: typeof REMOTE_PROTOCOL;
  readonly marimoExportVersion: string;
  readonly marimoVersion: string;
  readonly adapter: string;
  readonly projections: Readonly<Record<string, RemoteProjectionCapability>>;
}

export interface RemoteBuildReceipt {
  readonly elapsedMs: number;
  readonly scenarioCount: number;
  readonly projectionCount: number;
}

export interface RemoteBuildResult {
  readonly ref: ExportRef;
  readonly receipt: RemoteBuildReceipt;
}

export interface RemoteStage {
  readonly id: string;
  readonly url: string;
  readonly notebook_key: string | null;
  readonly expires_at_ms: number;
}

export interface RemoteReleaseResult {
  readonly released: boolean;
}

export interface RemoteTransport {
  describe(options?: RemoteRequestOptions): Promise<RemoteDescription>;
  build(plan: ExportPlan, options?: RemoteRequestOptions): Promise<RemoteBuildResult>;
  stage(ref: ExportRef, options?: RemoteRequestOptions): Promise<RemoteStage>;
  release(id: string, options?: RemoteRequestOptions): Promise<RemoteReleaseResult>;
}

interface SuccessEnvelope {
  protocol: typeof REMOTE_PROTOCOL;
  request_id: string;
  ok: true;
  data: unknown;
}

interface ErrorEnvelope {
  protocol: typeof REMOTE_PROTOCOL;
  request_id: string;
  ok: false;
  error: {
    code: string;
    message: string;
    details?: JsonObject;
  };
}

export function createRemoteTransport(options: RemoteTransportOptions): RemoteTransport {
  if (options.sessionId.length === 0) {
    throw new TypeError("sessionId must be a non-empty marimo session id.");
  }
  const enqueue = requestQueue(options.timeoutMs ?? 5 * 60_000);

  return {
    async describe(request = {}) {
      return enqueue("describe", request, async (queued) =>
        parseDescription(await dispatch(options, "describe", {}, queued)),
      );
    },
    async build(plan, request = {}) {
      const validated = validateExportPlan(plan);
      return enqueue("build", request, async (queued) => {
        const data = exactObject(
          await dispatch(options, "build", { plan: validated }, queued),
          ["ref", "receipt"],
          "build response",
        );
        return Object.freeze({
          ref: Object.freeze(parseExportRef(data.ref, "build response.ref")),
          receipt: parseBuildReceipt(data.receipt),
        });
      });
    },
    async stage(ref, request = {}) {
      return enqueue("stage", request, async (queued) => {
        const data = exactObject(
          await dispatch(options, "stage", { ref }, queued),
          ["id", "url", "notebook_key", "expires_at_ms"],
          "stage response",
        );
        return Object.freeze({
          id: nonEmptyString(data.id, "stage response.id"),
          url: nonEmptyString(data.url, "stage response.url"),
          notebook_key: nullableString(data.notebook_key, "stage response.notebook_key"),
          expires_at_ms: positiveInteger(data.expires_at_ms, "stage response.expires_at_ms"),
        });
      });
    },
    async release(id, request = {}) {
      if (id.length === 0) throw new TypeError("stage id must be non-empty.");
      return enqueue("release", request, async (queued) => {
        const data = exactObject(
          await dispatch(options, "release", { id }, queued),
          ["released"],
          "release response",
        );
        if (typeof data.released !== "boolean") {
          throw protocolError("release response.released must be a boolean.");
        }
        return Object.freeze({ released: data.released });
      });
    },
  };
}

function requestQueue(defaultTimeoutMs: number) {
  let tail = Promise.resolve();
  return async <T>(
    operation: RemoteOperation,
    request: RemoteRequestOptions,
    run: (request: RemoteRequestOptions) => Promise<T>,
  ): Promise<T> => {
    const abort = requestAbort(request.signal, request.timeoutMs ?? defaultTimeoutMs);
    const previous = tail;
    let release!: () => void;
    const slot = new Promise<void>((resolve) => {
      release = resolve;
    });
    tail = previous.catch(() => undefined).then(() => slot);
    try {
      await waitForTurn(previous, abort.signal);
      abort.signal.throwIfAborted();
      const result = await run({ ...request, signal: abort.signal });
      abort.signal.throwIfAborted();
      return result;
    } catch (error) {
      if (request.signal?.aborted === true) throw request.signal.reason;
      if (abort.timedOut()) {
        throw new MarimoExportError(
          "remote_timeout",
          `The client stopped waiting for remote ${operation} after ${abort.timeoutMs}ms. Remote work may still be running.`,
          { cause: error },
        );
      }
      throw error;
    } finally {
      release();
      abort.dispose();
    }
  };
}

async function waitForTurn(previous: Promise<void>, signal: AbortSignal): Promise<void> {
  if (signal.aborted) throw signal.reason;
  await new Promise<void>((resolve, reject) => {
    const aborted = () => reject(signal.reason);
    signal.addEventListener("abort", aborted, { once: true });
    void previous.then(resolve, reject).finally(() => {
      signal.removeEventListener("abort", aborted);
    });
  });
}

function parseDescription(input: unknown): RemoteDescription {
  const value = exactObject(
    input,
    ["protocol", "marimo_export_version", "marimo_version", "adapter", "projections"],
    "describe response",
  );
  if (value.protocol !== REMOTE_PROTOCOL) {
    throw protocolError(`describe response.protocol must be ${REMOTE_PROTOCOL}.`);
  }
  const capabilities = object(value.projections, "describe response.projections");
  const projections = Object.fromEntries(
    Object.entries(capabilities).map(([name, input]) => {
      const capability = exactObject(
        input,
        ["available", "extra"],
        `describe response.projections.${name}`,
      );
      if (typeof capability.available !== "boolean") {
        throw protocolError(`describe response.projections.${name}.available must be a boolean.`);
      }
      return [
        name,
        Object.freeze({
          available: capability.available,
          extra: nullableString(capability.extra, `describe response.projections.${name}.extra`),
        }),
      ];
    }),
  );
  return Object.freeze({
    protocol: REMOTE_PROTOCOL,
    marimoExportVersion: nonEmptyString(
      value.marimo_export_version,
      "describe response.marimo_export_version",
    ),
    marimoVersion: nonEmptyString(value.marimo_version, "describe response.marimo_version"),
    adapter: nonEmptyString(value.adapter, "describe response.adapter"),
    projections: Object.freeze(projections),
  });
}

function parseBuildReceipt(input: unknown): RemoteBuildReceipt {
  const value = exactObject(
    input,
    ["elapsed_ms", "scenario_count", "projection_count"],
    "build response.receipt",
  );
  if (
    typeof value.elapsed_ms !== "number" ||
    !Number.isFinite(value.elapsed_ms) ||
    value.elapsed_ms < 0
  ) {
    throw protocolError("build response.receipt.elapsed_ms must be a non-negative number.");
  }
  return Object.freeze({
    elapsedMs: value.elapsed_ms,
    scenarioCount: nonNegativeInteger(
      value.scenario_count,
      "build response.receipt.scenario_count",
    ),
    projectionCount: nonNegativeInteger(
      value.projection_count,
      "build response.receipt.projection_count",
    ),
  });
}

async function dispatch(
  options: RemoteTransportOptions,
  operation: RemoteOperation,
  params: Record<string, unknown>,
  requestOptions: RemoteRequestOptions,
): Promise<unknown> {
  const requestId = randomId();
  const requestJson = JSON.stringify({
    protocol: REMOTE_PROTOCOL,
    request_id: requestId,
    operation,
    params,
  });
  const code = [
    `request_json = ${JSON.stringify(requestJson)}`,
    "import marimo_export.remote as _marimo_export",
    "print(_marimo_export.RESPONSE_PREFIX + await _marimo_export.dispatch_json(request_json))",
  ].join("\n");
  const fetchImpl = options.fetch ?? globalThis.fetch;
  if (fetchImpl === undefined) {
    throw new MarimoExportError("remote_unavailable", "Remote marimo control requires fetch.");
  }

  const abort = requestAbort(
    requestOptions.signal,
    requestOptions.timeoutMs ?? options.timeoutMs ?? 5 * 60_000,
  );
  try {
    const response = await fetchImpl(new URL("api/kernel/execute", options.server), {
      method: "POST",
      headers: authHeaders(options, {
        "Content-Type": "application/json",
        "Marimo-Session-Id": options.sessionId,
      }),
      body: JSON.stringify({ code }),
      signal: abort.signal,
      redirect: "error",
    });
    abort.signal.throwIfAborted();
    if (response.redirected) {
      throw new MarimoExportError(
        "protocol_mismatch",
        "Remote control requests must not follow HTTP redirects.",
      );
    }
    if (!response.ok) {
      throw new MarimoExportError(
        "remote_request_failed",
        `marimo scratchpad request failed: ${response.status} ${response.statusText}.`,
      );
    }

    const execution = await readScratchpad(response);
    abort.signal.throwIfAborted();
    if (!execution.success) {
      throw new MarimoExportError(
        "remote_request_failed",
        execution.error ?? "marimo scratchpad execution failed.",
      );
    }
    const envelope = parseEnvelope(responseJson(execution.responseLine), requestId);
    if (!envelope.ok) {
      const code = isExportErrorCode(envelope.error.code)
        ? envelope.error.code
        : "remote_request_failed";
      const details = {
        ...envelope.error.details,
        ...(code === envelope.error.code ? {} : { remoteCode: envelope.error.code }),
      };
      throw new MarimoExportError(
        code,
        envelope.error.message,
        Object.keys(details).length === 0 ? {} : { details },
      );
    }
    abort.signal.throwIfAborted();
    return envelope.data;
  } catch (error) {
    if (requestOptions.signal?.aborted === true) throw requestOptions.signal.reason;
    if (abort.timedOut()) {
      throw new MarimoExportError(
        "remote_timeout",
        `The client stopped waiting for remote ${operation} after ${abort.timeoutMs}ms. Remote work may still be running.`,
        { cause: error },
      );
    }
    if (error instanceof MarimoExportError) throw error;
    throw new MarimoExportError("remote_request_failed", `Remote ${operation} request failed.`, {
      cause: error,
    });
  } finally {
    abort.dispose();
  }
}

function parseEnvelope(input: unknown, requestId: string): SuccessEnvelope | ErrorEnvelope {
  const root = object(input, "remote response");
  if (root.protocol !== REMOTE_PROTOCOL) {
    throw protocolError(
      `Remote protocol ${JSON.stringify(root.protocol)} does not match ${REMOTE_PROTOCOL}.`,
    );
  }
  if (root.request_id !== requestId) {
    throw protocolError("Remote response request id does not match the request.");
  }
  if (root.ok === true) {
    exactKeys(root, ["protocol", "request_id", "ok", "data"], "remote response");
    return { protocol: REMOTE_PROTOCOL, request_id: requestId, ok: true, data: root.data };
  }
  if (root.ok === false) {
    exactKeys(root, ["protocol", "request_id", "ok", "error"], "remote response");
    const error = object(root.error, "remote response.error");
    exactKeys(error, ["code", "message", "details"], "remote response.error", ["code", "message"]);
    return {
      protocol: REMOTE_PROTOCOL,
      request_id: requestId,
      ok: false,
      error: {
        code: nonEmptyString(error.code, "remote response.error.code"),
        message: nonEmptyString(error.message, "remote response.error.message"),
        ...(error.details === undefined
          ? {}
          : { details: jsonObject(error.details, "remote response.error.details") }),
      },
    };
  }
  throw protocolError("remote response.ok must be a boolean.");
}

interface ScratchpadExecution {
  success: boolean;
  responseLine?: string;
  error?: string;
}

async function readScratchpad(response: Response): Promise<ScratchpadExecution> {
  let responseLine: string | undefined;
  let success = false;
  let completed = false;
  let error: string | undefined;
  await readServerSentEvents(response, (event, input) => {
    const data = safeJson(input);
    if (event === "stdout") {
      const text = consoleText(data);
      const candidate = text === undefined ? undefined : findResponseLine(text);
      if (candidate !== undefined) responseLine = candidate;
      return;
    }
    if (event !== "done") return;
    completed = true;
    const done = object(data, "scratchpad done event");
    success = done.success === true;
    if (done.output !== undefined && done.output !== null) {
      const output = object(done.output, "scratchpad done output");
      const candidate = typeof output.data === "string" ? findResponseLine(output.data) : undefined;
      if (candidate !== undefined) responseLine = candidate;
    }
    if (!success && done.error !== undefined) {
      const failure = object(done.error, "scratchpad error");
      const type = typeof failure.type === "string" ? failure.type : "ScratchpadError";
      const message = typeof failure.msg === "string" ? failure.msg : "Execution failed.";
      error = `${type}: ${message}`;
    }
  });
  if (!completed) {
    throw new MarimoExportError(
      "protocol_mismatch",
      "marimo scratchpad stream ended before its done event.",
    );
  }
  return {
    success,
    ...(responseLine === undefined ? {} : { responseLine }),
    ...(error === undefined ? {} : { error }),
  };
}

async function readServerSentEvents(
  response: Response,
  onEvent: (event: string, data: string) => void,
): Promise<void> {
  if (response.body === null) {
    const text = await response.text();
    if (text.length > MAX_SSE_EVENT_CHARS) throw oversizedSse();
    parseEventText(text, onEvent);
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  try {
    while (true) {
      // oxlint-disable-next-line no-await-in-loop -- SSE chunks must be parsed in arrival order.
      const next = await reader.read();
      buffer += decoder.decode(next.value, { stream: !next.done });
      buffer = buffer.replaceAll("\r\n", "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        if (boundary > MAX_SSE_EVENT_CHARS) throw oversizedSse();
        parseEventBlock(buffer.slice(0, boundary), onEvent);
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
      if (buffer.length > MAX_SSE_EVENT_CHARS) throw oversizedSse();
      if (next.done) break;
    }
    parseEventBlock(buffer, onEvent);
  } catch (error) {
    try {
      void reader.cancel(error).catch(() => undefined);
    } catch {
      // The protocol failure remains authoritative when cancellation is unavailable.
    }
    throw error;
  } finally {
    reader.releaseLock();
  }
}

function parseEventText(text: string, onEvent: (event: string, data: string) => void): void {
  for (const block of text.replaceAll("\r\n", "\n").split("\n\n")) {
    if (block.length > MAX_SSE_EVENT_CHARS) throw oversizedSse();
    parseEventBlock(block, onEvent);
  }
}

function parseEventBlock(block: string, onEvent: (event: string, data: string) => void): void {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trimStart();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (data.length > 0) onEvent(event, data.join("\n"));
}

function findResponseLine(output: string): string | undefined {
  const lines = output.replaceAll("\r\n", "\n").split("\n");
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const line = lines[index];
    if (line === undefined || !line.startsWith(RESPONSE_PREFIX)) continue;
    const json = line.slice(RESPONSE_PREFIX.length).trim();
    if (json.length === 0) {
      throw protocolError("Remote worker returned an empty response envelope.");
    }
    if (
      json.length > MAX_RESPONSE_BYTES ||
      new TextEncoder().encode(json).byteLength > MAX_RESPONSE_BYTES
    ) {
      throw protocolError(`Remote worker response exceeds ${MAX_RESPONSE_BYTES} bytes.`);
    }
    return json;
  }
  return undefined;
}

function responseJson(line: string | undefined): unknown {
  if (line === undefined) {
    throw protocolError("Remote worker response prefix was missing from scratchpad output.");
  }
  try {
    return JSON.parse(line) as unknown;
  } catch (error) {
    throw protocolError("Remote worker returned malformed response JSON.", error);
  }
}

function oversizedSse(): MarimoExportError {
  return protocolError(`Remote scratchpad event exceeds ${MAX_SSE_EVENT_CHARS} characters.`);
}

export function authHeaders(
  options: Pick<RemoteTransportOptions, "headers" | "authToken" | "serverToken">,
  additional: Readonly<Record<string, string>> = {},
): Record<string, string> {
  const headers = new Headers(options.headers);
  for (const [name, value] of Object.entries(additional)) headers.set(name, value);
  if (options.authToken !== undefined) headers.set("Authorization", `Bearer ${options.authToken}`);
  if (options.serverToken !== undefined) {
    headers.set("Marimo-Server-Token", options.serverToken);
  }
  return Object.fromEntries(headers.entries());
}

function requestAbort(parent: AbortSignal | undefined, timeoutMs: number) {
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
    throw new TypeError("timeoutMs must be a positive integer.");
  }
  const controller = new AbortController();
  let timeout = false;
  const abort = () => controller.abort(parent?.reason);
  if (parent?.aborted === true) abort();
  else parent?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    timeout = true;
    controller.abort(new DOMException("Remote request timed out.", "TimeoutError"));
  }, timeoutMs);
  return {
    signal: controller.signal,
    timeoutMs,
    timedOut: () => timeout,
    dispose() {
      clearTimeout(timer);
      parent?.removeEventListener("abort", abort);
    },
  };
}

function consoleText(input: unknown): string | undefined {
  if (typeof input === "string") return input;
  if (typeof input === "object" && input !== null && !Array.isArray(input)) {
    const data = (input as Record<string, unknown>).data;
    if (typeof data === "string") return data;
  }
  return undefined;
}

function safeJson(input: string): unknown {
  try {
    return JSON.parse(input) as unknown;
  } catch {
    return input;
  }
}

function object(input: unknown, path: string): Record<string, unknown> {
  if (typeof input !== "object" || input === null || Array.isArray(input)) {
    throw protocolError(`${path} must be an object.`);
  }
  return input as Record<string, unknown>;
}

function exactObject(input: unknown, keys: readonly string[], path: string) {
  const value = object(input, path);
  exactKeys(value, keys, path);
  return value;
}

function exactKeys(
  value: Record<string, unknown>,
  allowed: readonly string[],
  path: string,
  required: readonly string[] = allowed,
): void {
  const extras = Object.keys(value).filter((key) => !allowed.includes(key));
  if (extras.length > 0) {
    throw protocolError(`${path} contains unexpected fields: ${extras.join(", ")}.`);
  }
  for (const key of required) {
    if (!(key in value)) throw protocolError(`${path}.${key} is required.`);
  }
}

function jsonObject(input: unknown, path: string): JsonObject {
  const value = object(input, path);
  try {
    return JSON.parse(JSON.stringify(value)) as JsonObject;
  } catch (error) {
    throw protocolError(`${path} must contain JSON values.`, error);
  }
}

function nonEmptyString(input: unknown, path: string): string {
  if (typeof input !== "string" || input.length === 0) {
    throw protocolError(`${path} must be a non-empty string.`);
  }
  return input;
}

function nullableString(input: unknown, path: string): string | null {
  if (input === null) return null;
  return nonEmptyString(input, path);
}

function nonNegativeInteger(input: unknown, path: string): number {
  if (typeof input !== "number" || !Number.isSafeInteger(input) || input < 0) {
    throw protocolError(`${path} must be a non-negative safe integer.`);
  }
  return input;
}

function positiveInteger(input: unknown, path: string): number {
  if (typeof input !== "number" || !Number.isSafeInteger(input) || input < 1) {
    throw protocolError(`${path} must be a positive safe integer.`);
  }
  return input;
}

function protocolError(message: string, cause?: unknown): MarimoExportError {
  return new MarimoExportError("protocol_mismatch", message, cause === undefined ? {} : { cause });
}

function randomId(): string {
  if (globalThis.crypto?.randomUUID === undefined) {
    throw new MarimoExportError(
      "remote_unavailable",
      "Remote marimo control requires crypto.randomUUID().",
    );
  }
  return globalThis.crypto.randomUUID();
}
