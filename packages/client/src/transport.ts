import type {
  CaptureClient,
  CaptureClientOptionsBase,
  CaptureFetch,
  ScratchpadExecutionResult,
  ScratchpadOutput,
} from "./types";
import { isRecord } from "./support";

interface ScratchpadDoneData {
  success: boolean;
  output?: ScratchpadOutput;
  error?: {
    type: string;
    msg: string;
    exception_type?: string;
  };
}

interface ScratchpadConsoleData {
  data: string;
}

interface ScratchpadEvent {
  event: string;
  data: unknown;
}

export function createPost(options: CaptureClientOptionsBase): CaptureClient["POST"] {
  const fetchImpl = options.fetch ?? globalFetch("export-client requires fetch.");
  const root = baseUrl(options.server);

  return async (path, postOptions = {}) => {
    const headers = requestHeaders(options);
    for (const [name, value] of Object.entries(postOptions.params?.header ?? {})) {
      headers.set(name, value);
    }
    const init: RequestInit = {
      method: "POST",
      headers,
    };

    if ("body" in postOptions) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(postOptions.body);
    }

    const response = await fetchImpl(new Request(new URL(path.replace(/^\//, ""), root), init));
    const text = await response.text();
    const data = text ? JSON.parse(text) : null;

    return { response, data };
  };
}

export function createScratchpadExecutor(
  options: CaptureClientOptionsBase,
): CaptureClient["executeScratchpad"] {
  const fetchImpl = options.fetch ?? globalFetch("export-client requires fetch.");
  const root = baseUrl(options.server);

  return async ({ code, sessionId, timeoutMs }) => {
    const headers = requestHeaders(options);
    headers.set("Content-Type", "application/json");
    headers.set("Marimo-Session-Id", sessionId);
    const controller = timeoutMs === undefined ? undefined : new AbortController();
    const timeout =
      controller === undefined ? undefined : setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetchImpl(
        new Request(new URL("api/kernel/execute", root), {
          method: "POST",
          headers,
          body: JSON.stringify({ code }),
          signal: controller?.signal ?? null,
        }),
      );
      const { ok, status, statusText } = response;

      if (!ok) {
        throw new Error(`Failed to execute marimo scratchpad: ${status} ${statusText}`);
      }

      return await readScratchpadExecution(response);
    } catch (error) {
      if (controller?.signal.aborted) {
        throw new Error(`Timed out executing marimo scratchpad after ${timeoutMs}ms.`);
      }
      throw error;
    } finally {
      if (timeout !== undefined) {
        clearTimeout(timeout);
      }
    }
  };
}

export function createNotebookOpener(
  options: CaptureClientOptionsBase,
): CaptureClient["openNotebook"] {
  const { server, WebSocket: WebSocketCtor = globalThis.WebSocket } = options;
  const fetchImpl = options.fetch ?? globalFetch("openNotebook requires fetch.");
  const root = baseUrl(server);

  return async ({ notebook, sessionId = randomSessionId(), timeoutMs = 30_000 }) => {
    if (typeof WebSocketCtor !== "function") {
      throw new Error("openNotebook requires WebSocket support. Pass WebSocket in client options.");
    }

    const url = notebookWebSocketUrl(server, notebook, sessionId, options);

    await new Promise<void>((resolve, reject) => {
      let ready = false;
      const socket = new WebSocketCtor(url);
      const timeout = setTimeout(() => {
        socket.close();
        reject(
          new Error(`Timed out opening marimo notebook session for ${JSON.stringify(notebook)}.`),
        );
      }, timeoutMs);

      socket.addEventListener("message", (event) => {
        const message = parseWebSocketMessage(event.data);
        if (message?.op !== "kernel-ready") {
          return;
        }

        ready = true;
        clearTimeout(timeout);
        socket.close();
        resolve();
      });

      socket.addEventListener("error", () => {
        clearTimeout(timeout);
        reject(
          new Error(`Failed to open marimo notebook session for ${JSON.stringify(notebook)}.`),
        );
      });

      socket.addEventListener("close", () => {
        clearTimeout(timeout);
        if (!ready) {
          reject(
            new Error(
              `marimo closed the notebook session before kernel-ready for ${JSON.stringify(notebook)}.`,
            ),
          );
        }
      });
    });

    await instantiateNotebook({ fetchImpl, options, root, sessionId });

    return {
      sessionId,
      name: notebookName(notebook),
      path: notebook,
      initializationId: null,
    };
  };
}

async function instantiateNotebook({
  fetchImpl,
  options,
  root,
  sessionId,
}: {
  fetchImpl: CaptureFetch;
  options: CaptureClientOptionsBase;
  root: string;
  sessionId: string;
}): Promise<void> {
  const headers = requestHeaders(options);
  headers.set("Content-Type", "application/json");
  headers.set("Marimo-Session-Id", sessionId);

  const response = await fetchImpl(
    new Request(new URL("api/kernel/instantiate", root), {
      method: "POST",
      headers,
      body: JSON.stringify({ objectIds: [], values: [], autoRun: true }),
    }),
  );
  const { ok, status, statusText } = response;

  if (!ok) {
    throw new Error(`Failed to instantiate marimo notebook: ${status} ${statusText}`);
  }
}

export function requestHeaders({
  headers: inputHeaders,
  serverToken,
  token,
}: CaptureClientOptionsBase): Headers {
  const headers = new Headers(inputHeaders);

  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  if (serverToken) {
    headers.set("Marimo-Server-Token", serverToken);
    if (!headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${serverToken}`);
    }
  }

  return headers;
}

export function baseUrl(server: string | URL): string {
  const value = server.toString();
  return value.endsWith("/") ? value : `${value}/`;
}

async function readScratchpadExecution(response: Response): Promise<ScratchpadExecutionResult> {
  const stdout: string[] = [];
  const stderr: string[] = [];
  const state: { done?: ScratchpadDoneData } = {};

  await readServerSentEvents(response, ({ event, data }) => {
    if (event === "stdout") {
      stdout.push(consoleData(data, event).data);
      return;
    }
    if (event === "stderr") {
      stderr.push(consoleData(data, event).data);
      return;
    }
    if (event === "done") {
      state.done = doneData(data);
    }
  });

  const { done } = state;

  if (!done) {
    throw new Error("marimo scratchpad stream ended without a done event.");
  }

  if (done.success === false) {
    const { error } = done;
    throw new Error(error ? `${error.type}: ${error.msg}` : "marimo scratchpad failed.");
  }

  return {
    success: true,
    output: done.output ?? null,
    stdout,
    stderr,
  };
}

async function readServerSentEvents(
  response: Response,
  onEvent: (event: ScratchpadEvent) => void,
): Promise<void> {
  if (!response.body) {
    readServerSentEventText(await response.text(), onEvent);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
    buffer = drainServerSentEventBuffer(buffer, onEvent);

    if (done) {
      readServerSentEventText(buffer, onEvent);
      return;
    }
  }
}

function drainServerSentEventBuffer(
  buffer: string,
  onEvent: (event: ScratchpadEvent) => void,
): string {
  let remaining = buffer;
  let boundary = remaining.indexOf("\n\n");

  while (boundary >= 0) {
    readServerSentEventText(remaining.slice(0, boundary), onEvent);
    remaining = remaining.slice(boundary + 2);
    boundary = remaining.indexOf("\n\n");
  }

  return remaining;
}

function readServerSentEventText(text: string, onEvent: (event: ScratchpadEvent) => void): void {
  const event = parseServerSentEvent(text.trim());
  if (event) {
    onEvent(event);
  }
}

function parseServerSentEvent(text: string): ScratchpadEvent | null {
  if (!text) {
    return null;
  }

  const lines = text.split("\n");
  const event = lines
    .find((line) => line.startsWith("event:"))
    ?.slice("event:".length)
    .trim();
  const data = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trimStart())
    .join("\n");

  if (!event) {
    return null;
  }

  return {
    event,
    data: data ? JSON.parse(data) : null,
  };
}

function consoleData(data: unknown, event: string): ScratchpadConsoleData {
  if (isRecord(data) && typeof data.data === "string") {
    return { data: data.data };
  }

  throw new Error(`Invalid marimo ${event} event payload.`);
}

function doneData(data: unknown): ScratchpadDoneData {
  if (isRecord(data) && typeof data.success === "boolean") {
    return data as unknown as ScratchpadDoneData;
  }

  throw new Error("Invalid marimo done event payload.");
}

function globalFetch(message: string): CaptureFetch {
  if (typeof globalThis.fetch !== "function") {
    throw new Error(`${message} Pass a fetch implementation.`);
  }

  return (request) => globalThis.fetch(request);
}

function notebookWebSocketUrl(
  server: string | URL,
  notebook: string,
  sessionId: string,
  options: CaptureClientOptionsBase,
): string {
  const url = new URL("ws", baseUrl(server));
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("session_id", sessionId);
  url.searchParams.set("file", notebook);

  const accessToken = options.serverToken ?? options.token;
  if (accessToken) {
    url.searchParams.set("access_token", accessToken);
  }

  return url.toString();
}

function parseWebSocketMessage(data: unknown): { op?: unknown } | null {
  if (typeof data !== "string") {
    return null;
  }

  try {
    const parsed: unknown = JSON.parse(data);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function randomSessionId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return `s_${globalThis.crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
  }

  return `s_${Math.random().toString(36).slice(2, 14)}`;
}

function notebookName(path: string): string {
  return path.split("/").filter(Boolean).at(-1) ?? path;
}
