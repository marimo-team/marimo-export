import { describe, expect, test, vi } from "vite-plus/test";

import {
  REMOTE_PROTOCOL,
  RESPONSE_PREFIX,
  authHeaders,
  createRemoteTransport,
} from "../src/remote/client.js";

const digest = "a".repeat(64);
const expiresAt = 2_000_000_000_000;
const ref = {
  key: `marimo-export/indexes/${digest}.json` as const,
  sha256: digest,
  size: 42,
};

describe("remote worker transport", () => {
  test("does not use the skew-protection token as an authentication credential", () => {
    expect(authHeaders({ serverToken: "skew-secret" })).toEqual({
      "marimo-server-token": "skew-secret",
    });
  });

  test("overrides case-insensitive reserved header collisions", () => {
    const headers = new Headers(
      authHeaders(
        {
          headers: {
            authorization: "User supplied",
            "MARIMO-SERVER-TOKEN": "wrong",
            "marimo-session-id": "wrong",
            "x-notebook-id": "wrong",
          },
          authToken: "auth-secret",
          serverToken: "server-secret",
        },
        {
          "Marimo-Session-Id": "session-1",
          "X-Notebook-Id": "notebook-1",
        },
      ),
    );

    expect(headers.get("Authorization")).toBe("Bearer auth-secret");
    expect(headers.get("Marimo-Server-Token")).toBe("server-secret");
    expect(headers.get("Marimo-Session-Id")).toBe("session-1");
    expect(headers.get("X-Notebook-Id")).toBe("notebook-1");
  });

  test("uses the fixed Python bridge and validates every response", async () => {
    const operations: string[] = [];
    const fetch = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      expect(init?.redirect).toBe("error");
      if (typeof init?.body !== "string") throw new TypeError("expected request body");
      const request = requestFrom(init.body);
      operations.push(request.operation);
      const code = (JSON.parse(init.body) as { code: string }).code;
      expect(code).toContain("import marimo_export.remote as _marimo_export");
      expect(code).not.toMatch(/pip|uv |base64|sys\.path|reload/);
      const responses: Record<string, unknown> = {
        describe: descriptionWire(),
        build: {
          ref,
          receipt: { elapsed_ms: 2.5, scenario_count: 2, projection_count: 4 },
        },
        stage: {
          id: "stage-1",
          url: "./public/stage-1/",
          notebook_key: "finance.py",
          expires_at_ms: expiresAt,
        },
        release: { released: true },
      };
      return scratchpadResponse(request.request_id, responses[request.operation]);
    });
    const remote = createRemoteTransport({
      server: new URL("https://marimo.test/"),
      sessionId: "session-1",
      fetch,
    });

    await expect(remote.describe()).resolves.toEqual(description());
    await expect(
      remote.build({
        schema: "marimo-export.plan.v1",
        outputs: { value: { source: "value", formats: { json: {} } } },
      }),
    ).resolves.toMatchObject({
      ref,
      receipt: { scenarioCount: 2, projectionCount: 4 },
    });
    await expect(remote.stage(ref)).resolves.toEqual({
      id: "stage-1",
      url: "./public/stage-1/",
      notebook_key: "finance.py",
      expires_at_ms: expiresAt,
    });
    await expect(remote.release("stage-1")).resolves.toEqual({ released: true });
    expect(operations).toEqual(["describe", "build", "stage", "release"]);
  });

  test("surfaces structured worker errors", async () => {
    const remote = createRemoteTransport({
      server: new URL("https://marimo.test/"),
      sessionId: "session-1",
      fetch: async (_input, init) => {
        if (typeof init?.body !== "string") throw new TypeError("expected request body");
        const request = requestFrom(init.body);
        return scratchpadResponse(request.request_id, undefined, {
          code: "unsupported_marimo",
          message: "Unsupported marimo version.",
        });
      },
    });

    await expect(remote.describe()).rejects.toMatchObject({ code: "unsupported_marimo" });
  });

  test("does not confuse a response marker inside a structured error message", async () => {
    const message = `Notebook raised ${RESPONSE_PREFIX} while computing the projection.`;
    const remote = createRemoteTransport({
      server: new URL("https://marimo.test/"),
      sessionId: "session-1",
      fetch: async (_input, init) => {
        if (typeof init?.body !== "string") throw new TypeError("expected request body");
        const request = requestFrom(init.body);
        return scratchpadResponse(request.request_id, undefined, {
          code: "build_failed",
          message,
        });
      },
    });

    await expect(remote.describe()).rejects.toMatchObject({ code: "build_failed", message });
  });

  test("normalizes unknown worker error codes", async () => {
    const remote = createRemoteTransport({
      server: new URL("https://marimo.test/"),
      sessionId: "session-1",
      fetch: async (_input, init) => {
        if (typeof init?.body !== "string") throw new TypeError("expected request body");
        const request = requestFrom(init.body);
        return scratchpadResponse(request.request_id, undefined, {
          code: "future_worker_error",
          message: "A newer worker rejected the request.",
        });
      },
    });

    await expect(remote.describe()).rejects.toMatchObject({
      code: "remote_request_failed",
      details: { remoteCode: "future_worker_error" },
    });
  });

  test("rejects extra receipt fields as a protocol mismatch", async () => {
    const remote = createRemoteTransport({
      server: new URL("https://marimo.test/"),
      sessionId: "session-1",
      fetch: async (_input, init) => {
        if (typeof init?.body !== "string") throw new TypeError("expected request body");
        const request = requestFrom(init.body);
        return scratchpadResponse(request.request_id, {
          ref,
          receipt: {
            elapsed_ms: 1,
            scenario_count: 1,
            projection_count: 1,
            projection_hits: 1,
          },
        });
      },
    });

    await expect(
      remote.build({
        schema: "marimo-export.plan.v1",
        outputs: { value: { source: "value", formats: { json: {} } } },
      }),
    ).rejects.toMatchObject({ code: "protocol_mismatch" });
  });

  test("parses CRLF event boundaries split across network chunks", async () => {
    const remote = createRemoteTransport({
      server: new URL("https://marimo.test/"),
      sessionId: "session-1",
      fetch: async (_input, init) => {
        if (typeof init?.body !== "string") throw new TypeError("expected request body");
        const request = requestFrom(init.body);
        const text = await scratchpadResponse(request.request_id, descriptionWire()).text();
        const crlf = text.replaceAll("\n", "\r\n");
        const split = crlf.indexOf("\r\n");
        const encoder = new TextEncoder();
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode(crlf.slice(0, split + 1)));
            controller.enqueue(encoder.encode(crlf.slice(split + 1)));
            controller.close();
          },
        });
        return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
      },
    });

    await expect(remote.describe()).resolves.toEqual(description());
  });

  test("does not accumulate unrelated scratchpad stdout", async () => {
    const remote = createRemoteTransport({
      server: new URL("https://marimo.test/"),
      sessionId: "session-1",
      fetch: async (_input, init) => {
        if (typeof init?.body !== "string") throw new TypeError("expected request body");
        const request = requestFrom(init.body);
        const noise = Array.from(
          { length: 1_100 },
          () => `event: stdout\ndata: ${JSON.stringify({ data: "x".repeat(2_000) })}\n\n`,
        ).join("");
        const response = await scratchpadResponse(request.request_id, descriptionWire()).text();
        return new Response(`${noise}${response}`);
      },
    });

    await expect(remote.describe()).resolves.toEqual(description());
  });

  test("rejects an oversized incomplete scratchpad event", async () => {
    let cancellations = 0;
    const remote = createRemoteTransport({
      server: new URL("https://marimo.test/"),
      sessionId: "session-1",
      fetch: async () => {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode("x".repeat(2 * 1024 * 1024)));
          },
          cancel() {
            cancellations += 1;
          },
        });
        return new Response(body);
      },
    });

    await expect(remote.describe()).rejects.toMatchObject({ code: "protocol_mismatch" });
    expect(cancellations).toBe(1);
  });

  test("rejects a custom fetch that followed a redirect", async () => {
    const remote = createRemoteTransport({
      server: new URL("https://marimo.test/"),
      sessionId: "session-1",
      fetch: async () => redirectedResponse(),
    });

    await expect(remote.describe()).rejects.toMatchObject({ code: "protocol_mismatch" });
  });

  test("rejects an invalid plan before making a remote request", async () => {
    const fetch = vi.fn();
    const remote = createRemoteTransport({
      server: new URL("https://marimo.test/"),
      sessionId: "session-1",
      fetch,
    });

    await expect(
      remote.build({ schema: "marimo-export.plan.v1", outputs: {} }),
    ).rejects.toMatchObject({ code: "invalid_plan" });
    expect(fetch).not.toHaveBeenCalled();
  });

  test("cancels a queued request without dispatching or interrupting the active request", async () => {
    const activeResponse = deferred<Response>();
    const activeStarted = deferred<void>();
    const fetch = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      if (typeof init?.body !== "string") throw new TypeError("expected request body");
      activeStarted.resolve();
      return activeResponse.promise;
    });
    const remote = createRemoteTransport({
      server: new URL("https://marimo.test/"),
      sessionId: "session-1",
      fetch,
    });

    const active = remote.describe();
    await activeStarted.promise;
    const controller = new AbortController();
    const queued = remote.describe({ signal: controller.signal });
    controller.abort(new DOMException("cancelled", "AbortError"));

    await expect(queued).rejects.toMatchObject({ name: "AbortError" });
    expect(fetch).toHaveBeenCalledOnce();
    const body = fetch.mock.calls[0]?.[1]?.body;
    if (typeof body !== "string") throw new TypeError("expected request body");
    const request = requestFrom(body);
    activeResponse.resolve(scratchpadResponse(request.request_id, descriptionWire()));
    await expect(active).resolves.toEqual(description());
  });
});

function description() {
  return {
    protocol: REMOTE_PROTOCOL,
    marimoExportVersion: "0.0.0",
    marimoVersion: "0.23.14",
    adapter: "marimo-0.23",
    projections: {
      json: { available: true, extra: null },
      arrow: { available: false, extra: "dataframe" },
    },
  };
}

function descriptionWire() {
  const value = description();
  return {
    protocol: value.protocol,
    marimo_export_version: value.marimoExportVersion,
    marimo_version: value.marimoVersion,
    adapter: value.adapter,
    projections: value.projections,
  };
}

function scratchpadResponse(
  requestId: string,
  data: unknown,
  error?: { code: string; message: string },
): Response {
  const envelope =
    error === undefined
      ? { protocol: REMOTE_PROTOCOL, request_id: requestId, ok: true, data }
      : { protocol: REMOTE_PROTOCOL, request_id: requestId, ok: false, error };
  const stdout = JSON.stringify({ data: `${RESPONSE_PREFIX}${JSON.stringify(envelope)}\n` });
  const done = JSON.stringify({ success: true, output: null });
  return new Response(`event: stdout\ndata: ${stdout}\n\nevent: done\ndata: ${done}\n\n`, {
    headers: { "Content-Type": "text/event-stream" },
  });
}

function requestFrom(bodyText: string): { request_id: string; operation: string } {
  const body = JSON.parse(bodyText) as { code: string };
  const assignment = body.code.split("\n", 1)[0];
  if (assignment === undefined || !assignment.startsWith("request_json = ")) {
    throw new TypeError("expected request assignment");
  }
  const requestJson = JSON.parse(assignment.slice("request_json = ".length)) as string;
  return JSON.parse(requestJson) as { request_id: string; operation: string };
}

function redirectedResponse(): Response {
  const response = new Response(null, { status: 200 });
  Object.defineProperty(response, "redirected", { value: true });
  return response;
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
