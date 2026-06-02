import assert from "node:assert/strict";
import test from "node:test";
import {
  createMarimoExportClient as createServerMarimoExportClient,
  createMarimoWorkspaceClient as createServerMarimoWorkspaceClient,
  type ExportResult,
  type MarimoExportClient,
  type MarimoExportClientOptions,
  type MarimoWorkspaceClient,
  type ExportSpec,
} from "@marimo-team/export-client";
import {
  createMarimoExportClient as createBrowserMarimoExportClient,
  createMarimoWorkspaceClient as createBrowserMarimoWorkspaceClient,
} from "@marimo-team/export-client/browser";

interface ScratchpadRequest {
  code: string;
  sessionId: string | null;
  url: string;
}

type ExportClientFactory = (options: MarimoExportClientOptions) => MarimoExportClient;
type WorkspaceClientFactory = (options: MarimoExportClientOptions) => MarimoWorkspaceClient;
type ExportFetch = NonNullable<MarimoExportClientOptions["fetch"]>;
type ExportPayload = Omit<
  ExportResult,
  "sessionId" | "sessionName" | "sessionPath" | "sessionInitializationId"
>;

const spec = {
  scenarios: [{ id: "default" }],
  values: {
    title: {
      source: { def: "title" },
      formats: ["text"],
    },
  },
} satisfies ExportSpec;

const exportPayload = {
  bundlePath: "/tmp/export/bundles/sha256-demo",
  manifestPath: "/tmp/export/bundles/sha256-demo/manifest.json",
  invocationPath: "/tmp/export/bundles/sha256-demo/traces/invocation.json",
  invocationIndexPath: "/tmp/export/bundles/sha256-demo/traces/index.json",
  manifest: { id: "sha256-demo" },
  invocation: { id: "invocation-demo" },
} satisfies ExportPayload;

const clientFactories: Array<{ name: string; create: ExportClientFactory }> = [
  { name: "server entry", create: createServerMarimoExportClient },
  { name: "browser entry", create: createBrowserMarimoExportClient },
];

const workspaceFactories: Array<{ name: string; create: WorkspaceClientFactory }> = [
  { name: "server entry", create: createServerMarimoWorkspaceClient },
  { name: "browser entry", create: createBrowserMarimoWorkspaceClient },
];

test("MarimoExportClient exports bundles and archives through one public interface", async (t) => {
  for (const factory of clientFactories) {
    await t.test(factory.name, async () => {
      await assertExport(factory.create);
    });
  }
});

test("MarimoWorkspaceClient lists sessions and workspace notebooks", async (t) => {
  for (const factory of workspaceFactories) {
    await t.test(factory.name, async () => {
      await assertLists(factory.create);
    });
  }
});

test("MarimoExportClient reports missing preinstalled runtime through the import probe", async (t) => {
  for (const factory of clientFactories) {
    await t.test(factory.name, async () => {
      const requests: ScratchpadRequest[] = [];
      const client = factory.create({
        server: "https://marimo.example.test",
        fetch: scratchpadFetch(requests, false),
      });

      await assert.rejects(
        client.export(spec, {
          sessionId: "session-1",
          runtime: "preinstalled",
        }),
        /moexport is not importable/,
      );
      assert.equal(requests.length, 1);
    });
  }
});

test("MarimoExportClient propagates scratchpad HTTP failures from the import probe", async (t) => {
  for (const factory of clientFactories) {
    await t.test(factory.name, async () => {
      const client = factory.create({
        server: "https://marimo.example.test",
        fetch: async () =>
          new Response("scratchpad failed", {
            status: 500,
            statusText: "Server Error",
          }),
      });

      await assert.rejects(
        client.export(spec, {
          sessionId: "session-1",
          runtime: "preinstalled",
        }),
        /Failed to execute marimo scratchpad: 500 Server Error/,
      );
    });
  }
});

async function assertLists(createClient: WorkspaceClientFactory): Promise<void> {
  const requests: string[] = [];
  const client = createClient({
    server: "https://marimo.example.test",
    fetch: listFetch(requests),
  });

  assert.deepEqual(await client.sessions.list(), [
    {
      sessionId: "session-1",
      name: "finance.py",
      path: "/work/finance.py",
      initializationId: "init-1",
    },
  ]);
  assert.deepEqual(await client.notebooks.list(), [
    {
      id: "finance",
      name: "finance.py",
      path: "/work/notebooks/finance.py",
    },
  ]);
  assert.equal(await client.notebooks.source("/work/notebooks/finance.py"), "# Finance\n");
  assert.deepEqual(requests, [
    "/api/home/running_notebooks",
    "/api/home/workspace_files",
    "/api/files/file_details",
  ]);
}

async function assertExport(createClient: ExportClientFactory): Promise<void> {
  const requests: ScratchpadRequest[] = [];
  const client = createClient({
    server: "https://marimo.example.test",
    fetch: scratchpadFetch(requests),
  });

  const result = await client.export(spec, {
    sessionId: "session-1",
    outputRoot: "/tmp/export",
    paths: ["/tmp/local-exporters"],
    runtime: "preinstalled",
  });
  const archive = await client.archive(spec, {
    sessionId: "session-1",
    paths: ["/tmp/local-exporters"],
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
  assert.equal(result.bundlePath, exportPayload.bundlePath);
  assert.equal(result.manifestPath, exportPayload.manifestPath);
  assert.deepEqual(result.manifest, exportPayload.manifest);
  assert.deepEqual(result.invocation, exportPayload.invocation);
  assert.equal(result.sessionId, "session-1");
  assert.equal(result.sessionName, null);
  assert.equal(result.sessionPath, null);
  assert.equal(result.sessionInitializationId, null);
  assert.equal(requests[1]?.code.includes("/tmp/local-exporters"), true);
  assert.equal(requests[3]?.code.includes("/tmp/local-exporters"), true);
  assert.equal(requests[1]?.code.includes('to="/tmp/export"'), true);
  assert.equal(requests[3]?.code.includes("to="), false);
  assert.deepEqual([...archive.bytes], [...new TextEncoder().encode("zip-bytes")]);
  assert.equal(archive.mediaType, "application/vnd.marimo.static-export+zip");
  assert.equal(archive.sessionId, "session-1");
}

function scratchpadFetch(requests: ScratchpadRequest[], canImport = true): ExportFetch {
  return async (request) => {
    const code = requestCode(await request.json());

    requests.push({
      code,
      sessionId: request.headers.get("Marimo-Session-Id"),
      url: request.url,
    });

    if (hasMarker(code, "RESULT")) {
      return scratchpadResponse(exportStdout(code, exportPayload));
    }
    if (hasMarker(code, "ARCHIVE")) {
      return scratchpadResponse(archiveStdout(code));
    }
    if (hasMarker(code, "IMPORT")) {
      return scratchpadResponse(importStdout(code, canImport));
    }
    return scratchpadResponse();
  };
}

function listFetch(requests: string[]): ExportFetch {
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
    if (path === "/api/files/file_details") {
      return Response.json({ contents: "# Finance\n" });
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

function exportStdout(code: string, payload: ExportPayload): string {
  const marker = resultMarker(code, "RESULT");
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
  const marker = resultMarker(code, "ARCHIVE");
  return `${marker}${Buffer.from("zip-bytes").toString("base64")}\n`;
}

function importStdout(code: string, value: boolean): string {
  const marker = resultMarker(code, "IMPORT");
  return `${marker}${JSON.stringify(value)}\n`;
}

function resultMarker(code: string, kind: "RESULT" | "ARCHIVE" | "IMPORT"): string {
  const marker = code.match(markerPattern(kind))?.[0];
  assert.ok(marker);
  return marker;
}

function hasMarker(code: string, kind: "RESULT" | "ARCHIVE" | "IMPORT"): boolean {
  return markerPattern(kind).test(code);
}

function markerPattern(kind: "RESULT" | "ARCHIVE" | "IMPORT"): RegExp {
  return new RegExp(`__MOEXPORT_${kind}_[0-9]+_[a-z0-9]+__`);
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
