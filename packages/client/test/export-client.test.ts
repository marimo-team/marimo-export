import assert from "node:assert/strict";
import test from "node:test";
import {
  createExportClient as createServerExportClient,
  type CaptureResult,
  type ExportClient,
  type ExportClientOptions,
  type ExportSpecInput,
} from "@marimo-team/export-client";
import {
  createExportClient as createBrowserExportClient,
  type BrowserExportClient,
} from "@marimo-team/export-client/browser";

interface ScratchpadRequest {
  code: string;
  sessionId: string | null;
  url: string;
}

type ExportClientFactory = (options: ExportClientOptions) => ExportClient | BrowserExportClient;
type CaptureFetch = NonNullable<ExportClientOptions["fetch"]>;

const spec = {
  scenarios: [{ id: "default" }],
  values: {
    title: {
      source: { def: "title" },
      artifacts: ["text"],
    },
  },
} satisfies ExportSpecInput;

const capturePayload = {
  bundlePath: "/tmp/export/bundles/sha256-demo",
  manifestPath: "/tmp/export/bundles/sha256-demo/manifest.json",
  invocationPath: "/tmp/export/bundles/sha256-demo/traces/invocation.json",
  invocationIndexPath: "/tmp/export/bundles/sha256-demo/traces/index.json",
  manifest: { id: "sha256-demo" },
  invocation: { id: "invocation-demo" },
} satisfies Omit<CaptureResult, "session">;

const clientFactories: Array<{ name: string; create: ExportClientFactory }> = [
  { name: "server entry", create: createServerExportClient },
  { name: "browser entry", create: createBrowserExportClient },
];

test("ExportClient captures bundles and archives through one public interface", async (t) => {
  for (const factory of clientFactories) {
    await t.test(factory.name, async () => {
      await assertCapture(factory.create);
    });
  }
});

test("ExportClient lists sessions and workspace notebooks through one public interface", async (t) => {
  for (const factory of clientFactories) {
    await t.test(factory.name, async () => {
      await assertLists(factory.create);
    });
  }
});

async function assertLists(createExportClient: ExportClientFactory): Promise<void> {
  const requests: string[] = [];
  const client = createExportClient({
    server: "https://marimo.example.test",
    fetch: listFetch(requests),
  });

  assert.deepEqual(await client.listSessions(), [
    {
      sessionId: "session-1",
      name: "finance.py",
      path: "/work/finance.py",
      initializationId: "init-1",
    },
  ]);
  assert.deepEqual(await client.listWorkspaceNotebooks(), [
    {
      id: "finance",
      name: "finance.py",
      path: "/work/notebooks/finance.py",
    },
  ]);
  assert.deepEqual(requests, ["/api/home/running_notebooks", "/api/home/workspace_files"]);
}

async function assertCapture(createExportClient: ExportClientFactory): Promise<void> {
  const requests: ScratchpadRequest[] = [];
  const client = createExportClient({
    server: "https://marimo.example.test",
    fetch: scratchpadFetch(requests),
  });

  const result = await client.capture(spec, {
    sessionId: "session-1",
    to: "/tmp/export",
    runtime: "preinstalled",
  });
  const archive = await client.captureArchive(spec, {
    sessionId: "session-1",
    runtime: "preinstalled",
  });

  assert.equal(requests.length, 4);
  assert.deepEqual(
    requests.map((request) => request.sessionId),
    ["session-1", "session-1", "session-1", "session-1"],
  );
  assert.deepEqual(
    requests.map((request) => new URL(request.url).pathname),
    ["/api/kernel/execute", "/api/kernel/execute", "/api/kernel/execute", "/api/kernel/execute"],
  );
  assert.equal(result.bundlePath, capturePayload.bundlePath);
  assert.equal(result.manifestPath, capturePayload.manifestPath);
  assert.deepEqual(result.manifest, capturePayload.manifest);
  assert.deepEqual(result.invocation, capturePayload.invocation);
  assert.deepEqual(result.session, {
    sessionId: "session-1",
    name: null,
    path: null,
    initializationId: null,
  });
  assert.deepEqual([...archive.bytes], [...new TextEncoder().encode("zip-bytes")]);
  assert.equal(archive.mediaType, "application/vnd.marimo.static-export+zip");
  assert.equal(archive.session.sessionId, "session-1");
}

function scratchpadFetch(requests: ScratchpadRequest[]): CaptureFetch {
  return async (request) => {
    const code = requestCode(await request.json());

    requests.push({
      code,
      sessionId: request.headers.get("Marimo-Session-Id"),
      url: request.url,
    });

    if (code.includes("find_spec")) {
      return scratchpadResponse();
    }
    if (code.includes("emit_bundle_archive")) {
      return scratchpadResponse(archiveStdout(code));
    }

    return scratchpadResponse(captureStdout(code, capturePayload));
  };
}

function listFetch(requests: string[]): CaptureFetch {
  return async (request) => {
    const path = new URL(request.url).pathname;
    requests.push(path);
    if (path === "/api/home/running_notebooks") {
      return Response.json({
        files: [
          {
            sessionId: "session-1",
            name: "finance.py",
            path: "/work/finance.py",
            initializationId: "init-1",
          },
          { name: "missing-session.py" },
        ],
      });
    }
    if (path === "/api/home/workspace_files") {
      return Response.json({
        files: [
          {
            id: "folder",
            isMarimoFile: false,
            name: "notebooks",
            path: "/work/notebooks",
            children: [
              {
                id: "finance",
                isMarimoFile: true,
                name: "finance.py",
                path: "/work/notebooks/finance.py",
              },
            ],
          },
        ],
      });
    }
    throw new Error(`unexpected request: ${path}`);
  };
}

function requestCode(body: unknown): string {
  assert.ok(isRecord(body));
  const { code } = body;
  if (typeof code !== "string") {
    throw new Error("scratchpad request body must include code");
  }
  return code;
}

function captureStdout(code: string, payload: Omit<CaptureResult, "session">): string {
  const markerMatch = /print\(("__MOEXPORT_CAPTURE_[^"]+__") \+ json\.dumps/.exec(code);
  assert.ok(markerMatch?.[1]);
  const marker = JSON.parse(markerMatch[1]) as string;
  return `${marker}${JSON.stringify({
    bundle_path: payload.bundlePath,
    manifest_path: payload.manifestPath,
    invocation_path: payload.invocationPath,
    invocation_index_path: payload.invocationIndexPath,
    manifest: payload.manifest,
    invocation: payload.invocation,
  })}\n`;
}

function archiveStdout(code: string): string {
  const markerMatch = /marker=("__MOEXPORT_ARCHIVE_[^"]+__")/.exec(code);
  assert.ok(markerMatch?.[1]);
  const marker = JSON.parse(markerMatch[1]) as string;
  return `${marker}${Buffer.from("zip-bytes").toString("base64")}\n`;
}

function scratchpadResponse(stdout?: string): Response {
  const events = [
    ...(stdout === undefined ? [] : [{ event: "stdout", data: { data: stdout } }]),
    { event: "done", data: { success: true } },
  ];
  return new Response(
    events.map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(""),
    {
      headers: {
        "Content-Type": "text/event-stream",
      },
    },
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
