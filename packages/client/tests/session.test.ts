import { describe, expect, test, vi } from "vite-plus/test";

import {
  connectRemote as connectRemoteUnderTest,
  type ConnectRemoteOptions,
} from "../src/remote/session.js";

const ref = {
  key: `marimo-export/indexes/${"a".repeat(64)}.json` as const,
  sha256: "a".repeat(64),
  size: 1,
};
const expiresAt = 2_000_000_000_000;

describe("remote session lifecycle", () => {
  test("closes a borrowed explicit session locally", async () => {
    const fetch = vi.fn(async () => {
      throw new Error("fetch should not run");
    });
    const remote = await connectRemote({
      server: "https://marimo.test",
      target: { sessionId: "s_explicit" },
      fetch,
    });

    expect(remote.session).toEqual({ id: "s_explicit", name: null, path: null, owned: false });
    await remote.close();
    expect(fetch).not.toHaveBeenCalled();
  });

  test("opens with auto-run disabled and closes the owned session", async () => {
    ReadyWebSocket.instances.length = 0;
    const calls: Array<{ path: string; body: unknown }> = [];
    const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(urlOf(input));
      const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : null;
      calls.push({ path: url.pathname, body });
      if (url.pathname === "/base/api/home/running_notebooks") {
        return jsonResponse({ files: [] });
      }
      if (url.pathname === "/base/api/home/shutdown_session") {
        return jsonResponse({ files: [] });
      }
      return new Response(null, { status: 204 });
    });
    const remote = await connectRemote({
      server: "https://marimo.test/base",
      target: { notebook: "examples/_notebooks/finance.py" },
      serverToken: "server-secret",
      fetch,
      WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
    });

    expect(calls[0]).toEqual({ path: "/base/api/home/running_notebooks", body: null });
    expect(calls[1]).toMatchObject({
      path: "/base/api/kernel/instantiate",
      body: { objectIds: [], values: [], autoRun: false },
    });
    expect(new URL(ReadyWebSocket.urls.at(-1)!).searchParams.get("file")).toBe(
      "examples/_notebooks/finance.py",
    );
    expect(remote.session).toMatchObject({
      name: "finance.py",
      path: "examples/_notebooks/finance.py",
      owned: true,
    });
    expect(ReadyWebSocket.instances).toHaveLength(1);
    expect(ReadyWebSocket.instances[0]?.closeCalls).toBe(0);
    await remote.close();
    await remote.close();
    expect(ReadyWebSocket.instances[0]?.closeCalls).toBe(1);
    expect(calls[2]).toEqual({
      path: "/base/api/home/shutdown_session",
      body: { sessionId: remote.session.id },
    });
    expect(calls[3]).toEqual({ path: "/base/api/home/running_notebooks", body: null });
    expect(fetch).toHaveBeenCalledTimes(4);
  });

  test("waits until the exact owned session disappears", async () => {
    let shutdown = false;
    let verificationCalls = 0;
    let sessionId = "";
    const remote = await connectRemote({
      server: "https://marimo.test",
      target: { notebook: "examples/_notebooks/finance.py" },
      WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
      fetch: async (input, init) => {
        const path = new URL(urlOf(input)).pathname;
        if (path === "/api/kernel/instantiate") return new Response(null, { status: 204 });
        if (path === "/api/home/shutdown_session") {
          shutdown = true;
          sessionId = jsonRequest<{ sessionId: string }>(init).sessionId;
          return jsonResponse({ files: [{ sessionId }] });
        }
        if (!shutdown) return jsonResponse({ files: [] });
        verificationCalls += 1;
        return jsonResponse({
          files:
            verificationCalls === 1
              ? [{ sessionId }, { sessionId: "s_other" }]
              : [{ sessionId: "s_other" }],
        });
      },
    });

    await expect(remote.close()).resolves.toBeUndefined();
    expect(verificationCalls).toBe(2);
  });

  test("keeps close pending when shutdown responds before session removal", async () => {
    const listing = deferred<Response>();
    let shutdown = false;
    const remote = await connectRemote({
      server: "https://marimo.test",
      target: { notebook: "examples/_notebooks/finance.py" },
      WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
      fetch: async (input) => {
        const path = new URL(urlOf(input)).pathname;
        if (path === "/api/kernel/instantiate") return new Response(null, { status: 204 });
        if (path === "/api/home/shutdown_session") {
          shutdown = true;
          return jsonResponse({ files: [] });
        }
        return shutdown ? listing.promise : jsonResponse({ files: [] });
      },
    });

    let closed = false;
    const closing = remote.close().then(() => {
      closed = true;
    });
    await Promise.resolve();
    await Promise.resolve();
    expect(closed).toBe(false);
    listing.resolve(jsonResponse({ files: [] }));
    await closing;
    expect(closed).toBe(true);
  });

  test("bounds session-removal verification and permits cleanup retry", async () => {
    let sessionId = "";
    let shutdownCalls = 0;
    let verificationCalls = 0;
    let present = true;
    const remote = await connectRemote({
      server: "https://marimo.test",
      target: { notebook: "examples/_notebooks/finance.py" },
      WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
      fetch: async (input, init) => {
        const path = new URL(urlOf(input)).pathname;
        if (path === "/api/kernel/instantiate") return new Response(null, { status: 204 });
        if (path === "/api/home/shutdown_session") {
          shutdownCalls += 1;
          sessionId = jsonRequest<{ sessionId: string }>(init).sessionId;
          return jsonResponse({ files: [{ sessionId }] });
        }
        if (sessionId.length === 0) return jsonResponse({ files: [] });
        verificationCalls += 1;
        return jsonResponse({ files: present ? [{ sessionId }] : [] });
      },
    });

    await expect(remote.close({ timeoutMs: 5 })).rejects.toMatchObject({
      code: "remote_timeout",
    });
    expect(shutdownCalls).toBe(1);
    expect(verificationCalls).toBe(1);
    present = false;
    await expect(remote.close()).resolves.toBeUndefined();
    expect(shutdownCalls).toBe(2);
    expect(verificationCalls).toBe(2);
    expect(ReadyWebSocket.instances.at(-1)?.closeCalls).toBe(1);
  });

  test("keeps a resumed notebook session running after disconnect", async () => {
    ReadyWebSocket.ready = { resumed: true, kiosk: false, auto_instantiated: false };
    const fetch = vi.fn(async () => jsonResponse({ files: [] }));
    try {
      const remote = await connectRemote({
        server: "https://marimo.test",
        target: { notebook: "/srv/examples/_notebooks/finance.py" },
        fetch,
        WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
      });

      expect(remote.session).toMatchObject({
        path: "/srv/examples/_notebooks/finance.py",
        owned: false,
      });
      await remote.close();
      expect(fetch).toHaveBeenCalledOnce();
      expect(ReadyWebSocket.instances.at(-1)?.closeCalls).toBe(1);
    } finally {
      ReadyWebSocket.ready = { resumed: false, kiosk: false, auto_instantiated: false };
    }
  });

  test("rejects a notebook target when marimo routes it to an active kiosk session", async () => {
    ReadyWebSocket.ready = { resumed: true, kiosk: true, auto_instantiated: false };
    const fetch = vi.fn(async () => jsonResponse({ files: [] }));
    try {
      await expect(
        connectRemote({
          server: "https://marimo.test",
          target: { notebook: "examples/_notebooks/finance.py" },
          fetch,
          WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
        }),
      ).rejects.toMatchObject({
        code: "session_open_failed",
        message:
          "The notebook is already active. Connect with a primary session ID from GET /api/sessions.",
      });
      expect(fetch).toHaveBeenCalledOnce();
      expect(ReadyWebSocket.instances.at(-1)?.closeCalls).toBe(1);
    } finally {
      ReadyWebSocket.ready = { resumed: false, kiosk: false, auto_instantiated: false };
    }
  });

  test("keeps authentication and skew-protection tokens in their upstream roles", async () => {
    ReadyWebSocket.urls.length = 0;
    const headers: Headers[] = [];
    const remote = await connectRemote({
      server: "https://marimo.test",
      target: { notebook: "examples/_notebooks/finance.py" },
      authToken: "auth-secret",
      serverToken: "skew-secret",
      WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
      fetch: async (input, init) => {
        headers.push(new Headers(init?.headers));
        const path = new URL(urlOf(input)).pathname;
        if (path === "/api/home/running_notebooks") return jsonResponse({ files: [] });
        if (path === "/api/home/shutdown_session") return jsonResponse({});
        return new Response(null, { status: 204 });
      },
    });
    await remote.close();

    expect(new URL(ReadyWebSocket.urls[0]!).searchParams.get("access_token")).toBe("auth-secret");
    for (const value of headers) {
      expect(value.get("Authorization")).toBe("Bearer auth-secret");
      expect(value.get("Marimo-Server-Token")).toBe("skew-secret");
    }
  });

  test("stages one HTTP source and releases it once", async () => {
    const operations: string[] = [];
    const payload = new Uint8Array([1, 2, 3]);
    const notebookKey = "notebooks/測試%2Ffinance.py";
    const remote = await connectRemote({
      server: "https://marimo.test",
      target: { sessionId: "s_explicit" },
      serverToken: "secret",
      fetch: async (input, init) => {
        const url = new URL(urlOf(input));
        if (url.pathname === "/public/stage-1/cache/value") {
          expect(init?.redirect).toBe("error");
          expect(new Headers(init?.headers).get("X-Notebook-Id")).toBe(
            encodeURIComponent(notebookKey),
          );
          return new Response(payload);
        }
        const request = remoteRequest(init);
        operations.push(request.operation);
        return remoteResponse(
          request.request_id,
          request.operation === "stage"
            ? {
                id: "stage-1",
                url: "/public/stage-1/",
                notebook_key: notebookKey,
                expires_at_ms: expiresAt,
              }
            : { released: true },
        );
      },
    });
    const lease = await remote.open(ref);
    expect(lease.expiresAt).toBe(expiresAt);
    await expect(lease.source.read("cache/value")).resolves.toEqual(payload);
    await lease.close();
    await lease.close();
    expect(operations).toEqual(["stage", "release"]);
  });

  test("releases a stage before rejecting a cross-origin source", async () => {
    const operations: string[] = [];
    const remote = await connectRemote({
      server: "https://marimo.test",
      target: { sessionId: "s_explicit" },
      fetch: async (_input, init) => {
        const request = remoteRequest(init);
        operations.push(request.operation);
        return remoteResponse(
          request.request_id,
          request.operation === "stage"
            ? {
                id: "stage-1",
                url: "https://other.test/export/",
                notebook_key: null,
                expires_at_ms: expiresAt,
              }
            : { released: true },
        );
      },
    });
    await expect(remote.open(ref)).rejects.toThrow("must use the marimo server origin");
    expect(operations).toEqual(["stage", "release"]);
  });

  test("rejects server URLs that could leak credentials or alter routing", async () => {
    await expect(
      connectRemote({
        server: "https://user:secret@marimo.test",
        target: { sessionId: "s" },
      }),
    ).rejects.toThrow("must not contain credentials");
    await expect(
      connectRemote({ server: "https://marimo.test/?x=1", target: { sessionId: "s" } }),
    ).rejects.toThrow("must not contain a query or fragment");
  });

  test("runtime-validates that a target selects exactly one field", async () => {
    await expect(
      connectRemote({
        server: "https://marimo.test",
        target: { notebook: "finance.py", sessionId: "s" } as never,
      }),
    ).rejects.toThrow("exactly one of notebook or sessionId");
  });

  test("best-effort closes a new session when instantiation fails", async () => {
    const paths: string[] = [];
    await expect(
      connectRemote({
        server: "https://marimo.test",
        target: { notebook: "examples/_notebooks/finance.py" },
        WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
        fetch: async (input) => {
          const path = new URL(urlOf(input)).pathname;
          paths.push(path);
          if (path === "/api/home/running_notebooks") return jsonResponse({ files: [] });
          if (path.endsWith("/api/kernel/instantiate")) {
            return new Response(null, { status: 500, statusText: "failed" });
          }
          if (path === "/api/home/shutdown_session") return jsonResponse({ files: [] });
          return new Response(null, { status: 204 });
        },
      }),
    ).rejects.toMatchObject({ code: "session_open_failed" });
    expect(paths).toEqual([
      "/api/home/running_notebooks",
      "/api/kernel/instantiate",
      "/api/home/shutdown_session",
      "/api/home/running_notebooks",
    ]);
  });

  test("rejects invalid skew credentials before opening a WebSocket", async () => {
    const before = ReadyWebSocket.instances.length;
    const paths: string[] = [];
    const fetch = vi.fn(async (input) => {
      const path = new URL(urlOf(input)).pathname;
      paths.push(path);
      if (path === "/api/status") return editStatusResponse();
      return jsonResponse({ error: "Invalid server token" }, { status: 401 });
    });

    await expect(
      connectRemote({
        server: "https://marimo.test",
        target: { notebook: "examples/_notebooks/finance.py" },
        serverToken: "wrong",
        fetch,
        WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
      }),
    ).rejects.toMatchObject({ code: "session_open_failed" });
    expect(paths).toEqual(["/api/home/running_notebooks", "/api/status"]);
    expect(ReadyWebSocket.instances).toHaveLength(before);
  });

  test("maps a readable run server to unsupported_mode before opening a notebook", async () => {
    const paths: string[] = [];
    const before = ReadyWebSocket.instances.length;

    await expect(
      connectRemoteUnderTest({
        server: "https://marimo.test",
        target: { notebook: "examples/_notebooks/finance.py" },
        WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
        fetch: async (input) => {
          const path = new URL(urlOf(input)).pathname;
          paths.push(path);
          if (path === "/api/home/running_notebooks" || path === "/api/status") {
            return new Response(null, { status: 401 });
          }
          if (path === "/api/version") return new Response("0.23.14");
          throw new Error(`unexpected request: ${path}`);
        },
      }),
    ).rejects.toMatchObject({
      code: "unsupported_mode",
      message: "Remote marimo control requires edit mode. Start the server with `marimo edit`.",
    });
    expect(paths).toEqual(["/api/home/running_notebooks", "/api/status", "/api/version"]);
    expect(ReadyWebSocket.instances).toHaveLength(before);
  });

  test("maps a readable run server to unsupported_mode before attaching a session", async () => {
    const paths: string[] = [];

    await expect(
      connectRemoteUnderTest({
        server: "https://marimo.test",
        target: { sessionId: "s_primary" },
        fetch: async (input) => {
          const path = new URL(urlOf(input)).pathname;
          paths.push(path);
          if (path === "/api/sessions") return new Response(null, { status: 401 });
          if (path === "/api/version") return new Response("0.23.14");
          throw new Error(`unexpected request: ${path}`);
        },
      }),
    ).rejects.toMatchObject({
      code: "unsupported_mode",
      message: "Remote marimo control requires edit mode. Start the server with `marimo edit`.",
    });
    expect(paths).toEqual(["/api/sessions", "/api/version"]);
  });

  test("validates explicit targets against the primary session registry", async () => {
    const paths: string[] = [];

    await expect(
      connectRemoteUnderTest({
        server: "https://marimo.test",
        target: { sessionId: "consumer_or_stale" },
        fetch: async (input) => {
          const path = new URL(urlOf(input)).pathname;
          paths.push(path);
          if (path === "/api/sessions") {
            return jsonResponse({ s_primary: { filename: "finance.py", path: "/srv/finance.py" } });
          }
          throw new Error(`unexpected request: ${path}`);
        },
      }),
    ).rejects.toMatchObject({
      code: "session_open_failed",
      message:
        'marimo session "consumer_or_stale" is not an active primary session. Use a session ID returned by GET /api/sessions.',
    });
    expect(paths).toEqual(["/api/sessions"]);
  });

  test("rejects an invalid primary session registry before remote control", async () => {
    await expect(
      connectRemoteUnderTest({
        server: "https://marimo.test",
        target: { sessionId: "s_primary" },
        fetch: async () => jsonResponse({ s_primary: { filename: 42, path: null } }),
      }),
    ).rejects.toMatchObject({
      code: "session_open_failed",
      message: "marimo returned an invalid session registry.",
    });
  });

  test("validates edit mode and a primary session before remote control", async () => {
    const paths: string[] = [];
    const remote = await connectRemoteUnderTest({
      server: "https://marimo.test",
      target: { sessionId: "s_primary" },
      authToken: "auth-secret",
      fetch: async (input, init) => {
        const path = new URL(urlOf(input)).pathname;
        paths.push(path);
        if (path === "/api/sessions") {
          expect(init?.redirect).toBe("error");
          expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer auth-secret");
          return jsonResponse({ s_primary: { filename: "finance.py", path: "/srv/finance.py" } });
        }
        const request = remoteRequest(init);
        return remoteResponse(request.request_id, {
          protocol: "marimo-export.remote.v1",
          marimo_export_version: "0.0.0",
          marimo_version: "0.23.14",
          adapter: "v1",
          projections: {},
        });
      },
    });

    await expect(remote.describe()).resolves.toMatchObject({
      protocol: "marimo-export.remote.v1",
    });
    await remote.close();
    expect(paths).toEqual(["/api/sessions", "/api/kernel/execute"]);
  });

  test("releases a stage that finishes opening after remote close begins", async () => {
    const stageResponse = deferred<Response>();
    const stageStarted = deferred<void>();
    const operations: string[] = [];
    let stageRequestId = "";
    const remote = await connectRemote({
      server: "https://marimo.test",
      target: { sessionId: "s_explicit" },
      fetch: async (_input, init) => {
        const request = remoteRequest(init);
        operations.push(request.operation);
        if (request.operation === "stage") {
          stageRequestId = request.request_id;
          stageStarted.resolve();
          return stageResponse.promise;
        }
        return remoteResponse(request.request_id, { released: true });
      },
    });

    const opening = remote.open(ref);
    await stageStarted.promise;
    const closing = remote.close();
    const stageRequest = operations[0];
    expect(stageRequest).toBe("stage");
    stageResponse.resolve(
      remoteResponse(stageRequestId, {
        id: "stage-late",
        url: "/public/stage-late/",
        notebook_key: null,
        expires_at_ms: expiresAt,
      }),
    );

    await expect(opening).rejects.toMatchObject({ code: "remote_closed" });
    await expect(closing).resolves.toBeUndefined();
    expect(operations).toEqual(["stage", "release"]);
  });

  test("waits for an active build before shutting down an owned session", async () => {
    const buildResponse = deferred<Response>();
    const buildStarted = deferred<void>();
    let buildRequestId = "";
    let shutdown = false;
    const remote = await connectRemote({
      server: "https://marimo.test",
      target: { notebook: "examples/_notebooks/finance.py" },
      WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
      fetch: async (input, init) => {
        const path = new URL(urlOf(input)).pathname;
        if (path === "/api/home/running_notebooks") return jsonResponse({ files: [] });
        if (path === "/api/kernel/instantiate") return new Response(null, { status: 204 });
        if (path === "/api/home/shutdown_session") {
          shutdown = true;
          return jsonResponse({});
        }
        const request = remoteRequest(init);
        buildRequestId = request.request_id;
        buildStarted.resolve();
        return buildResponse.promise;
      },
    });
    const building = remote.build({
      schema: "marimo-export.plan.v1",
      outputs: { value: { source: "value", formats: { json: {} } } },
    });
    await buildStarted.promise;

    const closing = remote.close();
    await Promise.resolve();
    expect(shutdown).toBe(false);
    buildResponse.resolve(
      remoteResponse(buildRequestId, {
        ref,
        receipt: { elapsed_ms: 1, scenario_count: 1, projection_count: 1 },
      }),
    );

    await expect(building).resolves.toEqual({
      ref,
      receipt: { elapsedMs: 1, scenarioCount: 1, projectionCount: 1 },
    });
    await expect(closing).resolves.toBeUndefined();
    expect(shutdown).toBe(true);
  });

  test("bounds close, closes its socket, and retries owned cleanup after active work", async () => {
    const response = deferred<Response>();
    const started = deferred<void>();
    let requestId = "";
    let shutdown = false;
    const remote = await connectRemote({
      server: "https://marimo.test",
      target: { notebook: "examples/_notebooks/finance.py" },
      WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
      fetch: async (input, init) => {
        const path = new URL(urlOf(input)).pathname;
        if (path === "/api/home/running_notebooks") return jsonResponse({ files: [] });
        if (path === "/api/kernel/instantiate") return new Response(null, { status: 204 });
        if (path === "/api/home/shutdown_session") {
          shutdown = true;
          return jsonResponse({});
        }
        const request = remoteRequest(init);
        requestId = request.request_id;
        started.resolve();
        return response.promise;
      },
    });
    const describing = remote.describe();
    await started.promise;

    await expect(remote.close({ timeoutMs: 5 })).rejects.toMatchObject({
      code: "remote_timeout",
    });
    expect(shutdown).toBe(false);
    expect(ReadyWebSocket.instances.at(-1)?.closeCalls).toBe(1);
    response.resolve(
      remoteResponse(requestId, {
        protocol: "marimo-export.remote.v1",
        marimo_export_version: "0.0.0",
        marimo_version: "0.18.0",
        adapter: "v1",
        projections: {},
      }),
    );
    await expect(describing).resolves.toMatchObject({
      protocol: "marimo-export.remote.v1",
      marimoExportVersion: "0.0.0",
    });
    await expect(remote.close()).resolves.toBeUndefined();
    expect(shutdown).toBe(true);
    expect(ReadyWebSocket.instances.at(-1)?.closeCalls).toBe(1);
  });

  test("retries a failed release before shutting down an owned session", async () => {
    const paths: string[] = [];
    let releaseAttempts = 0;
    const remote = await connectRemote({
      server: "https://marimo.test",
      target: { notebook: "examples/_notebooks/finance.py" },
      WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
      fetch: async (input, init) => {
        const path = new URL(urlOf(input)).pathname;
        paths.push(path);
        if (path === "/api/home/running_notebooks") return jsonResponse({ files: [] });
        if (path === "/api/kernel/instantiate") return new Response(null, { status: 204 });
        if (path === "/api/home/shutdown_session") return jsonResponse({});
        const request = remoteRequest(init);
        if (request.operation === "stage") {
          return remoteResponse(request.request_id, {
            id: "stage-retry",
            url: "/public/stage-retry/",
            notebook_key: null,
            expires_at_ms: expiresAt,
          });
        }
        releaseAttempts += 1;
        if (releaseAttempts === 1) throw new Error("temporary release failure");
        return remoteResponse(request.request_id, { released: true });
      },
    });
    await remote.open(ref);

    await expect(remote.close()).rejects.toMatchObject({ code: "remote_request_failed" });
    expect(paths).not.toContain("/api/home/shutdown_session");
    expect(ReadyWebSocket.instances.at(-1)?.closeCalls).toBe(1);
    await expect(remote.close()).resolves.toBeUndefined();
    expect(releaseAttempts).toBe(2);
    expect(paths.slice(-2)).toEqual(["/api/home/shutdown_session", "/api/home/running_notebooks"]);
    expect(ReadyWebSocket.instances.at(-1)?.closeCalls).toBe(1);
  });

  test("closes the local socket when owned-session shutdown fails and retries shutdown", async () => {
    let shutdownAttempts = 0;
    const remote = await connectRemote({
      server: "https://marimo.test",
      target: { notebook: "examples/_notebooks/finance.py" },
      WebSocket: ReadyWebSocket as unknown as typeof WebSocket,
      fetch: async (input) => {
        const path = new URL(urlOf(input)).pathname;
        if (path === "/api/home/running_notebooks") return jsonResponse({ files: [] });
        if (path === "/api/kernel/instantiate") return new Response(null, { status: 204 });
        shutdownAttempts += 1;
        if (shutdownAttempts === 1) throw new Error("temporary shutdown failure");
        return jsonResponse({});
      },
    });

    await expect(remote.close()).rejects.toMatchObject({ code: "session_close_failed" });
    expect(ReadyWebSocket.instances.at(-1)?.closeCalls).toBe(1);
    await expect(remote.close()).resolves.toBeUndefined();
    expect(shutdownAttempts).toBe(2);
    expect(ReadyWebSocket.instances.at(-1)?.closeCalls).toBe(1);
  });

  test("rejects an explicit session connection when its signal is already aborted", async () => {
    const controller = new AbortController();
    controller.abort(new DOMException("cancelled", "AbortError"));
    await expect(
      connectRemote({
        server: "https://marimo.test",
        target: { sessionId: "s_explicit" },
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ name: "AbortError" });
  });
});

function connectRemote(options: ConnectRemoteOptions) {
  const fallback =
    options.fetch ??
    (async () => {
      throw new Error("unexpected fetch request");
    });
  const sessionId = "sessionId" in options.target ? options.target.sessionId : undefined;
  return connectRemoteUnderTest({
    ...options,
    fetch: async (input, init) => {
      const path = new URL(urlOf(input)).pathname;
      if (path.endsWith("/api/sessions")) {
        return jsonResponse(
          sessionId === undefined ? {} : { [sessionId]: { filename: null, path: null } },
        );
      }
      return fallback(input, init);
    },
  });
}

class ReadyWebSocket {
  static readonly urls: string[] = [];
  static readonly instances: ReadyWebSocket[] = [];
  static ready = { resumed: false, kiosk: false, auto_instantiated: false };
  closeCalls = 0;
  private readonly listeners = new Map<string, Set<(event: MessageEvent) => void>>();

  constructor(url: string | URL) {
    ReadyWebSocket.urls.push(url.toString());
    ReadyWebSocket.instances.push(this);
    queueMicrotask(() =>
      this.emit("message", {
        data: JSON.stringify({ op: "kernel-ready", data: ReadyWebSocket.ready }),
      }),
    );
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    const listeners = this.listeners.get(type) ?? new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: (event: MessageEvent) => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  close(): void {
    this.closeCalls += 1;
  }

  private emit(type: string, event: { data: string }): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event as MessageEvent);
  }
}

function jsonResponse(value: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(value), {
    ...init,
    headers: { "Content-Type": "application/json" },
  });
}

function editStatusResponse(): Response {
  return jsonResponse({ mode: "edit" });
}

function urlOf(input: string | URL | Request): string {
  return input instanceof Request ? input.url : input instanceof URL ? input.href : input;
}

function remoteRequest(init: RequestInit | undefined): { request_id: string; operation: string } {
  if (typeof init?.body !== "string") throw new TypeError("expected request body");
  const body = JSON.parse(init.body) as { code: string };
  const assignment = body.code.split("\n", 1)[0];
  if (assignment === undefined) throw new TypeError("expected request assignment");
  return JSON.parse(JSON.parse(assignment.slice("request_json = ".length)) as string) as {
    request_id: string;
    operation: string;
  };
}

function jsonRequest<T>(init: RequestInit | undefined): T {
  if (typeof init?.body !== "string") throw new TypeError("expected JSON request body");
  return JSON.parse(init.body) as T;
}

function remoteResponse(requestId: string, data: unknown): Response {
  const envelope = {
    protocol: "marimo-export.remote.v1",
    request_id: requestId,
    ok: true,
    data,
  };
  const stdout = JSON.stringify({
    data: `__MARIMO_EXPORT_RESPONSE__:${JSON.stringify(envelope)}\n`,
  });
  const done = JSON.stringify({ success: true, output: null });
  return new Response(`event: stdout\ndata: ${stdout}\n\nevent: done\ndata: ${done}\n\n`);
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve(value: T | PromiseLike<T>): void;
} {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}
