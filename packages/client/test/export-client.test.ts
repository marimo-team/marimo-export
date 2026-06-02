import assert from "node:assert/strict";
import test from "node:test";
import {
  createMarimoExportClient as createServerMarimoExportClient,
  createMarimoExportClientFromTransport,
  createMarimoWorkspaceClient as createServerMarimoWorkspaceClient,
  parseExportSpec,
  type ExecuteScratchpadOptions,
  type ExportResult,
  type MarimoExportClient,
  type MarimoExportClientOptions,
  type MarimoExportTransport,
  type MarimoWorkspaceClient,
  type ScratchpadExecutionMetadata,
} from "@marimo-team/export-client";
import {
  createMarimoExportClient as createBrowserMarimoExportClient,
  createMarimoWorkspaceClient as createBrowserMarimoWorkspaceClient,
} from "@marimo-team/export-client/browser";

type ExportClientFactory = (options: MarimoExportClientOptions) => MarimoExportClient;
type WorkspaceClientFactory = (options: MarimoExportClientOptions) => MarimoWorkspaceClient;
type ExportFetch = NonNullable<MarimoExportClientOptions["fetch"]>;
type ExportPayload = Omit<
  ExportResult,
  "sessionId" | "sessionName" | "sessionPath" | "sessionInitializationId"
>;

const specInput = {
  scenarios: [{ id: "default" }],
  values: {
    title: {
      source: { def: "title" },
      formats: ["text"],
    },
  },
} as const;

const spec = parseExportSpec(specInput);

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

test("MarimoExportClient exports bundles and archives through one public interface", async () => {
  await assertExport();
});

test("MarimoWorkspaceClient lists sessions and workspace notebooks", async (t) => {
  for (const factory of workspaceFactories) {
    await t.test(factory.name, async () => {
      await assertLists(factory.create);
    });
  }
});

test("MarimoExportClient reports missing preinstalled runtime through the import probe", async () => {
  const requests: ExecuteScratchpadOptions[] = [];
  const client = createMarimoExportClientFromTransport(scratchpadTransport(requests, false));

  await assert.rejects(
    client.export(spec, {
      sessionId: "session-1",
      runtime: "preinstalled",
    }),
    /moexport is not importable/,
  );
  assert.equal(requests.length, 1);
  assertImportMetadata(requests[0]?.metadata);
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

async function assertExport(): Promise<void> {
  const requests: ExecuteScratchpadOptions[] = [];
  const client = createMarimoExportClientFromTransport(scratchpadTransport(requests));

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
  assert.equal(requests[0]?.metadata?.kind, "import");
  assert.equal(requests[2]?.metadata?.kind, "import");
  assertExportMetadata(requests[1]?.metadata);
  assertArchiveMetadata(requests[3]?.metadata);
  assert.equal(result.bundlePath, exportPayload.bundlePath);
  assert.equal(result.manifestPath, exportPayload.manifestPath);
  assert.deepEqual(result.manifest, exportPayload.manifest);
  assert.deepEqual(result.invocation, exportPayload.invocation);
  assert.equal(result.sessionId, "session-1");
  assert.equal(result.sessionName, null);
  assert.equal(result.sessionPath, null);
  assert.equal(result.sessionInitializationId, null);
  assert.deepEqual([...archive.bytes], [...new TextEncoder().encode("zip-bytes")]);
  assert.equal(archive.mediaType, "application/vnd.marimo.static-export+zip");
  assert.equal(archive.sessionId, "session-1");
}

function assertImportMetadata(metadata: ScratchpadExecutionMetadata | undefined): void {
  assert.ok(metadata);
  if (metadata.kind !== "import") {
    assert.fail(`expected import metadata, received ${metadata.kind}`);
  }
  assert.equal(metadata.moduleName, "moexport");
  assertMarker(metadata.marker);
}

function assertExportMetadata(metadata: ScratchpadExecutionMetadata | undefined): void {
  assert.ok(metadata);
  if (metadata.kind !== "export") {
    assert.fail(`expected export metadata, received ${metadata.kind}`);
  }
  assert.equal(metadata.outputRoot, "/tmp/export");
  assert.deepEqual(metadata.paths, ["/tmp/local-exporters"]);
  assert.deepEqual(metadata.spec, spec);
  assertMarker(metadata.marker);
}

function assertArchiveMetadata(metadata: ScratchpadExecutionMetadata | undefined): void {
  assert.ok(metadata);
  if (metadata.kind !== "archive") {
    assert.fail(`expected archive metadata, received ${metadata.kind}`);
  }
  assert.deepEqual(metadata.paths, ["/tmp/local-exporters"]);
  assert.deepEqual(metadata.spec, spec);
  assertMarker(metadata.marker);
}

function assertMarker(marker: string): void {
  assert.ok(marker.length > 0);
}

function scratchpadTransport(
  requests: ExecuteScratchpadOptions[],
  canImport = true,
): MarimoExportTransport {
  return {
    async POST() {
      throw new Error("unexpected POST call");
    },
    async executeScratchpad(request) {
      requests.push(request);
      const metadata = request.metadata;
      if (metadata?.kind === "export") {
        return scratchpadResult(exportStdout(metadata.marker, exportPayload));
      }
      if (metadata?.kind === "archive") {
        return scratchpadResult(archiveStdout(metadata.marker));
      }
      if (metadata?.kind === "import") {
        return scratchpadResult(importStdout(metadata.marker, canImport));
      }
      return scratchpadResult();
    },
    async openNotebook() {
      throw new Error("unexpected openNotebook call");
    },
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

function exportStdout(marker: string, payload: ExportPayload): string {
  return `${marker}${JSON.stringify({
    bundle_path: payload.bundlePath,
    manifest_path: payload.manifestPath,
    invocation_path: payload.invocationPath,
    invocation_index_path: payload.invocationIndexPath,
    manifest: payload.manifest,
    invocation: payload.invocation,
  })}\n`;
}

function archiveStdout(marker: string): string {
  return `${marker}${Buffer.from("zip-bytes").toString("base64")}\n`;
}

function importStdout(marker: string, value: boolean): string {
  return `${marker}${JSON.stringify(value)}\n`;
}

function scratchpadResult(stdout?: string) {
  return Promise.resolve({
    success: true,
    output: null,
    stdout: stdout === undefined ? [] : [stdout],
    stderr: [],
  });
}
