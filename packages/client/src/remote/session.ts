import { httpSource } from "../source.js";
import type { ExportRef, ExportSource } from "../types.js";
import { MarimoExportError } from "../types.js";
import { createRemoteBuild, type RemoteBuild } from "./build.js";
import {
  authHeaders,
  createRemoteTransport,
  type RemoteDescription,
  type RemoteRequestOptions,
  type RemoteTransport,
  type RemoteTransportOptions,
} from "./client.js";
import type { ExportPlan } from "./plan.js";

export type RemoteTarget =
  | { readonly notebook: string; readonly sessionId?: never }
  | { readonly sessionId: string; readonly notebook?: never };

export interface RemoteSession {
  readonly id: string;
  readonly name: string | null;
  readonly path: string | null;
  readonly owned: boolean;
}

export interface RemoteExportLease {
  readonly source: ExportSource;
  readonly expiresAt: number;
  close(options?: RemoteRequestOptions): Promise<void>;
}

export interface Remote {
  readonly session: RemoteSession;
  describe(options?: RemoteRequestOptions): Promise<RemoteDescription>;
  build(plan: ExportPlan, options?: RemoteRequestOptions): Promise<RemoteBuild>;
  open(ref: ExportRef, options?: RemoteRequestOptions): Promise<RemoteExportLease>;
  close(options?: RemoteRequestOptions): Promise<void>;
}

export interface ConnectRemoteOptions {
  server: string | URL;
  target: RemoteTarget;
  fetch?: typeof fetch;
  headers?: Readonly<Record<string, string>>;
  authToken?: string;
  serverToken?: string;
  timeoutMs?: number;
  connectTimeoutMs?: number;
  signal?: AbortSignal;
  WebSocket?: typeof WebSocket;
}

interface SessionConnection {
  readonly session: RemoteSession;
  readonly socket?: WebSocket;
}

const SESSION_REMOVAL_POLL_MS = 50;

export async function connectRemote(options: ConnectRemoteOptions): Promise<Remote> {
  options = Object.freeze({
    ...options,
    ...(options.headers === undefined ? {} : { headers: Object.freeze({ ...options.headers }) }),
  });
  const server = serverUrl(options.server);
  const target = validateTarget(options.target);
  const abort = sessionAbort(
    options.signal,
    options.connectTimeoutMs ?? 30_000,
    "connectTimeoutMs",
    "Remote session connection timed out.",
  );
  try {
    abort.signal.throwIfAborted();
    const connection =
      typeof target.sessionId === "string"
        ? await openExplicitSession(options, server, target.sessionId, abort.signal)
        : await openNotebook(options, server, target.notebook!, abort.signal);
    const session = connection.session;
    const transportOptions: RemoteTransportOptions = {
      server,
      sessionId: session.id,
      ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
      ...(options.headers === undefined ? {} : { headers: options.headers }),
      ...(options.authToken === undefined ? {} : { authToken: options.authToken }),
      ...(options.serverToken === undefined ? {} : { serverToken: options.serverToken }),
      ...(options.timeoutMs === undefined ? {} : { timeoutMs: options.timeoutMs }),
    };
    return createRemote(options, server, connection, createRemoteTransport(transportOptions));
  } catch (error) {
    if (options.signal?.aborted === true) throw options.signal.reason;
    if (abort.timedOut()) {
      throw new MarimoExportError(
        "session_timeout",
        `The client stopped waiting for the remote session after ${abort.timeoutMs}ms. Remote startup may still be running.`,
        { cause: error },
      );
    }
    throw error;
  } finally {
    abort.dispose();
  }
}

async function getServerEndpoint(
  options: ConnectRemoteOptions,
  server: URL,
  path: string,
  signal: AbortSignal,
): Promise<Response> {
  const fetchImpl = options.fetch ?? globalThis.fetch;
  if (fetchImpl === undefined) {
    throw new MarimoExportError("session_unavailable", "Remote connection requires fetch.");
  }
  try {
    return await fetchImpl(new URL(path, server), {
      headers: authHeaders(options),
      signal,
      redirect: "error",
    });
  } catch (error) {
    if (signal.aborted) throw signal.reason;
    throw new MarimoExportError("session_open_failed", "Remote server preflight failed.", {
      cause: error,
    });
  }
}

function unsupportedServerMode(): MarimoExportError {
  return new MarimoExportError(
    "unsupported_mode",
    "Remote marimo control requires edit mode. Start the server with `marimo edit`.",
  );
}

async function openExplicitSession(
  options: ConnectRemoteOptions,
  server: URL,
  sessionId: string,
  signal: AbortSignal,
): Promise<SessionConnection> {
  const response = await getServerEndpoint(options, server, "api/sessions", signal);
  rejectRedirect(response, "Remote session lookup");
  if (!response.ok) {
    if (await readableWithoutEditScope(options, server, response, signal, false)) {
      throw unsupportedServerMode();
    }
    discardResponse(response);
    throw new MarimoExportError(
      "session_open_failed",
      `Remote session lookup failed: ${response.status} ${response.statusText}.`,
    );
  }
  let sessions: unknown;
  try {
    sessions = await response.json();
  } catch (error) {
    throw invalidSessionRegistry(error);
  }
  if (!isRecord(sessions)) throw invalidSessionRegistry();
  for (const [id, value] of Object.entries(sessions)) {
    if (
      id.length === 0 ||
      !isRecord(value) ||
      !isNullableString(value.filename) ||
      !isNullableString(value.path)
    ) {
      throw invalidSessionRegistry();
    }
  }
  if (!Object.hasOwn(sessions, sessionId)) {
    throw new MarimoExportError(
      "session_open_failed",
      `marimo session ${JSON.stringify(sessionId)} is not an active primary session. Use a session ID returned by GET /api/sessions.`,
    );
  }
  return explicitSession(sessionId);
}

async function readableWithoutEditScope(
  options: ConnectRemoteOptions,
  server: URL,
  editResponse: Response,
  signal: AbortSignal,
  verifyEditScope: boolean,
): Promise<boolean> {
  if (editResponse.status !== 401 && editResponse.status !== 403) return false;
  discardResponse(editResponse);
  if (verifyEditScope) {
    const status = await getServerEndpoint(options, server, "api/status", signal);
    rejectRedirect(status, "Remote edit-scope verification");
    if (status.ok) {
      discardResponse(status);
      return false;
    }
    const editDenied = status.status === 401 || status.status === 403;
    discardResponse(status);
    if (!editDenied) return false;
  }
  const version = await getServerEndpoint(options, server, "api/version", signal);
  rejectRedirect(version, "Remote server mode verification");
  const readable = version.ok;
  discardResponse(version);
  return readable;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function invalidSessionRegistry(cause?: unknown): MarimoExportError {
  return new MarimoExportError(
    "session_open_failed",
    "marimo returned an invalid session registry.",
    cause === undefined ? {} : { cause },
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function createRemote(
  options: ConnectRemoteOptions,
  server: URL,
  connection: SessionConnection,
  transport: RemoteTransport,
): Remote {
  const { session } = connection;
  const leases = new Set<(options?: RemoteRequestOptions) => Promise<void>>();
  const openings = new Set<Promise<RemoteExportLease>>();
  const requests = new Set<Promise<unknown>>();
  let closePromise: Promise<void> | undefined;
  let sessionClosed = !session.owned;
  let socketClosed = connection.socket === undefined;
  let closed = false;

  const assertOpen = () => {
    if (closed) throw new MarimoExportError("remote_closed", "The remote connection is closed.");
  };

  const track = async <T>(operation: () => Promise<T>): Promise<T> => {
    assertOpen();
    const request = operation();
    requests.add(request);
    try {
      return await request;
    } finally {
      requests.delete(request);
    }
  };

  const remote: Remote = {
    session,
    async describe(request = {}) {
      return track(() => transport.describe(request));
    },
    async build(plan: ExportPlan, request: RemoteRequestOptions = {}) {
      return track(async () => createRemoteBuild(await transport.build(plan, request)));
    },
    async open(ref: ExportRef, request: RemoteRequestOptions = {}) {
      assertOpen();
      const opening = (async (): Promise<RemoteExportLease> => {
        const stage = await transport.stage(ref, request);
        const closeLease = retryable(async (closeOptions: RemoteRequestOptions = {}) => {
          await transport.release(stage.id, closeOptions);
          leases.delete(closeLease);
        });
        leases.add(closeLease);
        if (closed) {
          await closeLease().catch(() => undefined);
          throw new MarimoExportError("remote_closed", "The remote connection is closed.");
        }
        try {
          const root = stageUrl(stage.url, server);
          const headers = authHeaders(
            options,
            stage.notebook_key === null
              ? {}
              : { "X-Notebook-Id": encodeURIComponent(stage.notebook_key) },
          );
          return Object.freeze({
            source: httpSource(root, {
              fetch: noRedirectFetch(options.fetch ?? globalThis.fetch),
              headers,
            }),
            expiresAt: stage.expires_at_ms,
            close: closeLease,
          });
        } catch (error) {
          await closeLease().catch(() => undefined);
          throw error;
        }
      })();
      openings.add(opening);
      try {
        return await opening;
      } finally {
        openings.delete(opening);
      }
    },
    async close(request = {}) {
      if (closePromise !== undefined) return closePromise;
      closed = true;
      const operation = (async () => {
        const abort = sessionAbort(
          request.signal,
          request.timeoutMs ?? options.timeoutMs ?? 30_000,
          "timeoutMs",
          "Remote close timed out.",
        );
        let failure: unknown;
        try {
          try {
            await waitForSettled(requests, abort.signal);
          } catch (error) {
            failure ??= error;
          }
          try {
            await waitForSettled(openings, abort.signal);
          } catch (error) {
            failure ??= error;
          }
          try {
            const results = await waitForSettled(
              [...leases].map((closeLease) => closeLease({ ...request, signal: abort.signal })),
              abort.signal,
            );
            const failedLease = results.find(
              (result): result is PromiseRejectedResult => result.status === "rejected",
            );
            if (failedLease !== undefined) failure ??= failedLease.reason;
          } catch (error) {
            failure ??= error;
          }
          if (!sessionClosed && requests.size === 0 && openings.size === 0 && leases.size === 0) {
            try {
              await shutdownSession(options, server, session.id, {
                ...request,
                signal: abort.signal,
              });
              sessionClosed = true;
            } catch (error) {
              failure ??= error;
            }
          }
        } finally {
          if (!socketClosed) {
            try {
              connection.socket?.close();
              socketClosed = true;
            } catch (error) {
              failure ??= error;
            }
          }
          abort.dispose();
        }
        if (failure === undefined) return;
        if (request.signal?.aborted === true) throw request.signal.reason;
        if (abort.timedOut()) {
          throw new MarimoExportError(
            "remote_timeout",
            `The client stopped waiting for remote close after ${abort.timeoutMs}ms.`,
            { cause: failure },
          );
        }
        throw failure;
      })();
      closePromise = operation;
      try {
        await operation;
      } catch (error) {
        closePromise = undefined;
        throw error;
      }
    },
  };
  return Object.freeze(remote);
}

async function waitForSettled<T>(
  values: Iterable<PromiseLike<T>>,
  signal: AbortSignal,
): Promise<PromiseSettledResult<Awaited<T>>[]> {
  signal.throwIfAborted();
  const settled = Promise.allSettled(values);
  return new Promise((resolve, reject) => {
    const aborted = () => reject(signal.reason);
    signal.addEventListener("abort", aborted, { once: true });
    void settled.then(resolve, reject).finally(() => signal.removeEventListener("abort", aborted));
  });
}

async function openNotebook(
  options: ConnectRemoteOptions,
  server: URL,
  notebook: string,
  signal: AbortSignal,
): Promise<SessionConnection> {
  const WebSocketConstructor = options.WebSocket ?? globalThis.WebSocket;
  if (typeof WebSocketConstructor !== "function") {
    throw new MarimoExportError(
      "session_open_failed",
      "Opening a marimo notebook requires WebSocket support.",
    );
  }
  await preflightNotebookOpen(options, server, signal);
  const sessionId = `s_${randomId().replaceAll("-", "").slice(0, 12)}`;
  const websocket = new URL("ws", server);
  websocket.protocol = websocket.protocol === "https:" ? "wss:" : "ws:";
  websocket.searchParams.set("session_id", sessionId);
  websocket.searchParams.set("file", notebook);
  if (options.authToken !== undefined) {
    websocket.searchParams.set("access_token", options.authToken);
  }

  let handshake: KernelHandshake | undefined;
  try {
    handshake = await waitForKernel(WebSocketConstructor, websocket, notebook, signal);
    if (handshake.kiosk) {
      throw new MarimoExportError(
        "session_open_failed",
        "The notebook is already active. Connect with a primary session ID from GET /api/sessions.",
      );
    }
    if (!handshake.resumed && !handshake.kiosk && !handshake.autoInstantiated) {
      await instantiateNotebook(options, server, sessionId, signal);
    }
  } catch (error) {
    handshake?.socket.close();
    if (handshake !== undefined && !handshake.resumed && !handshake.kiosk) {
      await shutdownSession(options, server, sessionId, { timeoutMs: 10_000 }).catch(
        () => undefined,
      );
    }
    throw error;
  }
  return Object.freeze({
    session: Object.freeze({
      id: sessionId,
      name: notebookName(notebook),
      path: notebook,
      owned: !handshake.resumed && !handshake.kiosk,
    }),
    socket: handshake.socket,
  });
}

async function preflightNotebookOpen(
  options: ConnectRemoteOptions,
  server: URL,
  signal: AbortSignal,
): Promise<void> {
  const fetchImpl = options.fetch ?? globalThis.fetch;
  if (fetchImpl === undefined) {
    throw new MarimoExportError("session_unavailable", "Opening a notebook requires fetch.");
  }
  let response: Response;
  try {
    // This protected read validates auth and skew credentials before the WebSocket
    // connector can create or attach a session.
    response = await fetchImpl(new URL("api/home/running_notebooks", server), {
      method: "POST",
      headers: authHeaders(options),
      signal,
      redirect: "error",
    });
  } catch (error) {
    if (signal.aborted) throw signal.reason;
    throw new MarimoExportError("session_open_failed", "Remote session preflight failed.", {
      cause: error,
    });
  }
  discardResponse(response);
  rejectRedirect(response, "Remote session preflight");
  if (!response.ok) {
    if (await readableWithoutEditScope(options, server, response, signal, true)) {
      throw unsupportedServerMode();
    }
    throw new MarimoExportError(
      "session_open_failed",
      `Remote session preflight failed: ${response.status} ${response.statusText}.`,
    );
  }
}

interface KernelHandshake {
  readonly socket: WebSocket;
  readonly resumed: boolean;
  readonly kiosk: boolean;
  readonly autoInstantiated: boolean;
}

async function waitForKernel(
  WebSocketConstructor: typeof WebSocket,
  url: URL,
  notebook: string,
  signal: AbortSignal,
): Promise<KernelHandshake> {
  return new Promise<KernelHandshake>((resolve, reject) => {
    let settled = false;
    const socket = new WebSocketConstructor(url);
    const finish = (error?: unknown, ready?: Omit<KernelHandshake, "socket">) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", onAbort);
      socket.removeEventListener("message", onMessage);
      socket.removeEventListener("error", onError);
      socket.removeEventListener("close", onClose);
      if (error === undefined && ready !== undefined) resolve({ socket, ...ready });
      else {
        socket.close();
        reject(
          error ??
            new MarimoExportError(
              "session_open_failed",
              "marimo kernel-ready response did not report resumed state.",
            ),
        );
      }
    };
    const onAbort = () => finish(signal.reason);
    const onMessage = (event: MessageEvent) => {
      try {
        const ready = kernelReady(event.data);
        if (ready !== undefined) finish(undefined, ready);
      } catch (error) {
        finish(error);
      }
    };
    const onError = () =>
      finish(
        new MarimoExportError(
          "session_open_failed",
          `Failed to open marimo notebook ${JSON.stringify(notebook)}.`,
        ),
      );
    const onClose = () =>
      finish(
        new MarimoExportError(
          "session_open_failed",
          `marimo closed the session before ${JSON.stringify(notebook)} became ready.`,
        ),
      );
    signal.addEventListener("abort", onAbort, { once: true });
    socket.addEventListener("message", onMessage);
    socket.addEventListener("error", onError);
    socket.addEventListener("close", onClose);
    if (signal.aborted) onAbort();
  });
}

async function instantiateNotebook(
  options: ConnectRemoteOptions,
  server: URL,
  sessionId: string,
  signal: AbortSignal,
): Promise<void> {
  const fetchImpl = options.fetch ?? globalThis.fetch;
  if (fetchImpl === undefined) {
    throw new MarimoExportError("session_unavailable", "Notebook instantiation requires fetch.");
  }
  let response: Response;
  try {
    response = await fetchImpl(new URL("api/kernel/instantiate", server), {
      method: "POST",
      headers: authHeaders(options, {
        "Content-Type": "application/json",
        "Marimo-Session-Id": sessionId,
      }),
      body: JSON.stringify({ objectIds: [], values: [], autoRun: false }),
      signal,
      redirect: "error",
    });
  } catch (error) {
    if (signal.aborted) throw signal.reason;
    throw new MarimoExportError(
      "session_open_failed",
      `Failed to instantiate marimo notebook session ${sessionId}.`,
      { cause: error },
    );
  }
  rejectRedirect(response, "Notebook instantiation");
  if (!response.ok) {
    throw new MarimoExportError(
      "session_open_failed",
      `Failed to instantiate marimo notebook: ${response.status} ${response.statusText}.`,
    );
  }
}

async function shutdownSession(
  options: ConnectRemoteOptions,
  server: URL,
  sessionId: string,
  request: RemoteRequestOptions,
): Promise<void> {
  const fetchImpl = options.fetch ?? globalThis.fetch;
  if (fetchImpl === undefined) {
    throw new MarimoExportError("session_unavailable", "Session shutdown requires fetch.");
  }
  const abort = sessionAbort(
    request.signal,
    request.timeoutMs ?? options.timeoutMs ?? 30_000,
    "timeoutMs",
    "Remote session shutdown timed out.",
  );
  try {
    const response = await fetchImpl(new URL("api/home/shutdown_session", server), {
      method: "POST",
      headers: authHeaders(options, { "Content-Type": "application/json" }),
      body: JSON.stringify({ sessionId }),
      signal: abort.signal,
      redirect: "error",
    });
    rejectRedirect(response, "Session shutdown");
    discardResponse(response);
    if (!response.ok) {
      throw new MarimoExportError(
        "session_close_failed",
        `Failed to close marimo session: ${response.status} ${response.statusText}.`,
      );
    }
    await waitForSessionAbsent(options, server, sessionId, abort.signal);
  } catch (error) {
    if (request.signal?.aborted === true) throw request.signal.reason;
    if (abort.timedOut()) {
      throw new MarimoExportError(
        "session_close_failed",
        `The client stopped waiting for session shutdown after ${abort.timeoutMs}ms.`,
        { cause: error },
      );
    }
    if (error instanceof MarimoExportError) throw error;
    throw new MarimoExportError("session_close_failed", "Failed to close marimo session.", {
      cause: error,
    });
  } finally {
    abort.dispose();
  }
}

async function waitForSessionAbsent(
  options: ConnectRemoteOptions,
  server: URL,
  sessionId: string,
  signal: AbortSignal,
): Promise<void> {
  const fetchImpl = options.fetch ?? globalThis.fetch;
  if (fetchImpl === undefined) {
    throw new MarimoExportError("session_unavailable", "Session shutdown requires fetch.");
  }
  signal.throwIfAborted();
  const response = await fetchImpl(new URL("api/home/running_notebooks", server), {
    method: "POST",
    headers: authHeaders(options),
    signal,
    redirect: "error",
  });
  rejectRedirect(response, "Session shutdown verification");
  if (!response.ok) {
    discardResponse(response);
    throw new MarimoExportError(
      "session_close_failed",
      `Failed to verify marimo session shutdown: ${response.status} ${response.statusText}.`,
    );
  }
  if (!runningSessions(await response.json()).has(sessionId)) return;
  await waitForPoll(signal);
  return waitForSessionAbsent(options, server, sessionId, signal);
}

function runningSessions(value: unknown): ReadonlySet<string> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw invalidRunningNotebooksResponse();
  }
  const files = (value as Record<string, unknown>).files;
  if (!Array.isArray(files)) throw invalidRunningNotebooksResponse();
  const sessionIds = new Set<string>();
  for (const file of files) {
    if (typeof file !== "object" || file === null || Array.isArray(file)) {
      throw invalidRunningNotebooksResponse();
    }
    const sessionId = (file as Record<string, unknown>).sessionId;
    if (sessionId !== null && typeof sessionId !== "string") {
      throw invalidRunningNotebooksResponse();
    }
    if (sessionId !== null) sessionIds.add(sessionId);
  }
  return sessionIds;
}

function invalidRunningNotebooksResponse(): MarimoExportError {
  return new MarimoExportError(
    "session_close_failed",
    "marimo returned an invalid running-notebooks response while verifying session shutdown.",
  );
}

function waitForPoll(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(done, SESSION_REMOVAL_POLL_MS);
    const aborted = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", aborted);
      reject(signal.reason);
    };
    function done() {
      signal.removeEventListener("abort", aborted);
      resolve();
    }
    signal.addEventListener("abort", aborted, { once: true });
    if (signal.aborted) aborted();
  });
}

function stageUrl(value: string, server: URL): URL {
  const url = new URL(value, server);
  if (url.origin !== server.origin) {
    throw new MarimoExportError(
      "protocol_mismatch",
      "Remote stage URL must use the marimo server origin.",
    );
  }
  if (url.username.length > 0 || url.password.length > 0) {
    throw new MarimoExportError(
      "protocol_mismatch",
      "Remote stage URL must not contain credentials.",
    );
  }
  if (url.search.length > 0 || url.hash.length > 0) {
    throw new MarimoExportError(
      "protocol_mismatch",
      "Remote stage URL must not contain a query or fragment.",
    );
  }
  return new URL(url.pathname.endsWith("/") ? url.href : `${url.href}/`);
}

function serverUrl(value: string | URL): URL {
  const url = new URL(value);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new TypeError("marimo server URL must use HTTP or HTTPS.");
  }
  if (url.username.length > 0 || url.password.length > 0) {
    throw new TypeError("marimo server URL must not contain credentials.");
  }
  if (url.search.length > 0 || url.hash.length > 0) {
    throw new TypeError("marimo server URL must not contain a query or fragment.");
  }
  return new URL(url.pathname.endsWith("/") ? url.href : `${url.href}/`);
}

function validateTarget(target: RemoteTarget): RemoteTarget {
  if (typeof target !== "object" || target === null || Array.isArray(target)) {
    throw new TypeError("target must select a notebook or sessionId.");
  }
  const keys = Object.keys(target);
  if (keys.length !== 1 || (keys[0] !== "notebook" && keys[0] !== "sessionId")) {
    throw new TypeError("target must select exactly one of notebook or sessionId.");
  }
  if ("sessionId" in target) {
    if (typeof target.sessionId !== "string" || target.sessionId.length === 0) {
      throw new TypeError("target.sessionId must be non-empty.");
    }
    return Object.freeze({ sessionId: target.sessionId });
  }
  if (typeof target.notebook !== "string" || target.notebook.length === 0) {
    throw new TypeError("target.notebook must be non-empty.");
  }
  return Object.freeze({ notebook: target.notebook });
}

function explicitSession(id: string): SessionConnection {
  return Object.freeze({
    session: Object.freeze({ id, name: null, path: null, owned: false }),
  });
}

function kernelReady(input: unknown): Omit<KernelHandshake, "socket"> | undefined {
  if (typeof input !== "string") return undefined;
  let parsed: unknown;
  try {
    parsed = JSON.parse(input) as unknown;
  } catch {
    return undefined;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return undefined;
  const message = parsed as Record<string, unknown>;
  if (message.op !== "kernel-ready") return undefined;
  if (typeof message.data !== "object" || message.data === null || Array.isArray(message.data)) {
    throw new MarimoExportError(
      "session_open_failed",
      "marimo kernel-ready response.data must be an object.",
    );
  }
  const data = message.data as Record<string, unknown>;
  if (
    typeof data.resumed !== "boolean" ||
    typeof data.kiosk !== "boolean" ||
    typeof data.auto_instantiated !== "boolean"
  ) {
    throw new MarimoExportError(
      "session_open_failed",
      "marimo kernel-ready response must report resumed, kiosk, and auto_instantiated.",
    );
  }
  return {
    resumed: data.resumed,
    kiosk: data.kiosk,
    autoInstantiated: data.auto_instantiated,
  };
}

function notebookName(path: string): string {
  const parts = path.split(/[\\/]/).filter((part) => part.length > 0);
  return parts.at(-1) ?? path;
}

function randomId(): string {
  if (globalThis.crypto?.randomUUID === undefined) {
    throw new MarimoExportError(
      "session_unavailable",
      "Opening a marimo notebook requires crypto.randomUUID().",
    );
  }
  return globalThis.crypto.randomUUID();
}

function sessionAbort(
  parent: AbortSignal | undefined,
  timeoutMs: number,
  optionName: string,
  timeoutMessage: string,
) {
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
    throw new TypeError(`${optionName} must be a positive integer.`);
  }
  const controller = new AbortController();
  let timeout = false;
  const abort = () => controller.abort(parent?.reason);
  if (parent?.aborted === true) abort();
  else parent?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    timeout = true;
    controller.abort(new DOMException(timeoutMessage, "TimeoutError"));
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

function retryable<T extends unknown[]>(operation: (...args: T) => Promise<void>) {
  let complete = false;
  let promise: Promise<void> | undefined;
  return (...args: T): Promise<void> => {
    if (complete) return Promise.resolve();
    promise ??= operation(...args)
      .then(() => {
        complete = true;
      })
      .finally(() => {
        promise = undefined;
      });
    return promise;
  };
}

function noRedirectFetch(fetchImpl: typeof fetch | undefined): typeof fetch {
  if (fetchImpl === undefined) {
    throw new MarimoExportError("remote_unavailable", "Remote export reading requires fetch.");
  }
  return async (input, init = {}) => {
    const response = await fetchImpl(input, { ...init, redirect: "error" });
    rejectRedirect(response, "Remote export reading");
    return response;
  };
}

function rejectRedirect(response: Response, operation: string): void {
  if (response.redirected) {
    throw new MarimoExportError(
      "protocol_mismatch",
      `${operation} must not follow HTTP redirects.`,
    );
  }
}

function discardResponse(response: Response): void {
  if (response.body === null) return;
  try {
    void response.body.cancel().catch(() => undefined);
  } catch {
    // Status validation remains authoritative for custom responses.
  }
}
