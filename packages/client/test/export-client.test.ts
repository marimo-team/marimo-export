import assert from "node:assert/strict";
import test from "node:test";
import {
  createMarimoExportClient as createServerMarimoExportClient,
  type ExportResult,
  type MarimoExportClient,
  type MarimoExportClientOptions,
} from "@marimo-team/export-client";
import { createMarimoExportClient as createBrowserMarimoExportClient } from "@marimo-team/export-client/browser";
import { createMarimoExportClientFromTransport } from "../src/export-client.js";
import type {
  ExecuteScratchpadOptions,
  MarimoExportTransport,
  ScratchpadExecutionMetadata,
} from "../src/types.js";

type ExportClientFactory = (options: MarimoExportClientOptions) => MarimoExportClient;
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
} as const;

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

test("MarimoExportClient exports bundles and archives through one client", async () => {
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

  assert.deepEqual(
    requests.map((request) => request.sessionId),
    ["session-1", "session-1", "session-1", "session-1"],
  );
  assertImportMetadata(requests[0]?.metadata);
  assertExportMetadata(requests[1]?.metadata);
  assertImportMetadata(requests[2]?.metadata);
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
});

test("MarimoExportClient resolves a notebook name before export", async () => {
  const requests: ExecuteScratchpadOptions[] = [];
  const client = createMarimoExportClientFromTransport(
    scratchpadTransport(requests, {
      runningNotebooks: [
        {
          sessionId: "session-1",
          name: "finance.py",
          path: "/work/finance.py",
          initializationId: "init-1",
        },
      ],
    }),
  );

  const result = await client.export(spec, {
    notebook: "finance.py",
    runtime: "preinstalled",
  });

  assertImportMetadata(requests[0]?.metadata);
  assertExportMetadata(requests[1]?.metadata, { outputRoot: undefined, paths: undefined });
  assert.equal(result.sessionName, "finance.py");
  assert.equal(result.sessionPath, "/work/finance.py");
  assert.equal(result.sessionInitializationId, "init-1");
});

test("MarimoExportClient reports missing preinstalled runtime through the import probe", async () => {
  const requests: ExecuteScratchpadOptions[] = [];
  const client = createMarimoExportClientFromTransport(
    scratchpadTransport(requests, { canImport: false }),
  );

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

test("public entries propagate scratchpad HTTP failures from the import probe", async (t) => {
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

test("public entries validate runtime options before contacting marimo", async (t) => {
  for (const factory of clientFactories) {
    await t.test(factory.name, async () => {
      const requests: Request[] = [];
      const client = factory.create({
        server: "https://marimo.example.test",
        fetch: async (request) => {
          requests.push(request);
          return new Response("unexpected", { status: 500 });
        },
      });

      await assert.rejects(
        client.archive(spec, {
          sessionId: "session-1",
          // @ts-expect-error Runtime strings other than "preinstalled" are rejected before I/O.
          runtime: "bad",
        }),
        /runtime must be "preinstalled"/,
      );
      await assert.rejects(
        client.export(spec, {
          sessionId: "session-1",
          // @ts-expect-error Runtime install requests need a package specifier.
          runtime: { module: "moexport" },
        }),
        /runtime\.package/,
      );
      assert.equal(requests.length, 0);
    });
  }
});

function assertImportMetadata(metadata: ScratchpadExecutionMetadata | undefined): void {
  assert.ok(metadata);
  if (metadata.kind !== "import") {
    assert.fail(`expected import metadata, received ${metadata.kind}`);
  }
  assert.equal(metadata.moduleName, "moexport");
  assertMarker(metadata.marker);
}

function assertExportMetadata(
  metadata: ScratchpadExecutionMetadata | undefined,
  expected: { outputRoot: string | undefined; paths: readonly string[] | undefined } = {
    outputRoot: "/tmp/export",
    paths: ["/tmp/local-exporters"],
  },
): void {
  assert.ok(metadata);
  if (metadata.kind !== "export") {
    assert.fail(`expected export metadata, received ${metadata.kind}`);
  }
  assert.equal(metadata.outputRoot, expected.outputRoot);
  assert.deepEqual(metadata.paths, expected.paths);
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
  options: { canImport?: boolean; runningNotebooks?: unknown[] } = {},
): MarimoExportTransport {
  const canImport = options.canImport ?? true;
  return {
    async POST(path) {
      if (path === "/api/home/running_notebooks") {
        return {
          response: Response.json({}),
          data: { files: options.runningNotebooks ?? [] },
        };
      }
      throw new Error(`unexpected POST call: ${path}`);
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
