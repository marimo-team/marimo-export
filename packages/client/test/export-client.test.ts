import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  createMarimoExportClient as createServerMarimoExportClient,
  parseExportSpec,
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

const customFormatSpec = {
  values: {
    readout: {
      source: { def: "readout" },
      formats: [
        {
          format: "metrics",
          export: {
            type: "ref",
            ref: "metrics_exporters:readout",
          },
          options: { title: "Metrics Readout" },
        },
      ],
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
  assertGeneratedCodeRunsThroughMoexport(requests[1]?.code, {
    kind: "capture",
    spec: parseExportSpec(spec),
    to: "/tmp/export",
  });
  assertGeneratedCodeRunsThroughMoexport(requests[3]?.code, {
    kind: "archive",
    spec: parseExportSpec(spec),
  });
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

test("MarimoExportClient transports custom formats through the public export path", async () => {
  const requests: ExecuteScratchpadOptions[] = [];
  const client = createMarimoExportClientFromTransport(scratchpadTransport(requests));

  await client.export(customFormatSpec, {
    sessionId: "session-1",
    runtime: "preinstalled",
  });

  assertExportMetadata(requests[1]?.metadata, {
    outputRoot: undefined,
    paths: undefined,
    spec: parseExportSpec(customFormatSpec),
  });
  assertGeneratedCodeRunsThroughMoexport(requests[1]?.code, {
    kind: "capture",
    spec: parseExportSpec(customFormatSpec),
    to: null,
  });
  assert.deepEqual(readoutFormats(parseExportSpec(customFormatSpec)), [
    {
      format: "metrics",
      export: {
        type: "ref",
        ref: "metrics_exporters:readout",
      },
      options: { title: "Metrics Readout" },
    },
  ]);
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
  expected: {
    outputRoot: string | undefined;
    paths: readonly string[] | undefined;
    spec?: unknown;
  } = {
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
  assert.deepEqual(metadata.spec, expected.spec ?? spec);
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

function assertGeneratedCodeRunsThroughMoexport(
  code: string | undefined,
  expected: { kind: "capture" | "archive"; spec: unknown; to?: string | null },
): void {
  assert.ok(code);
  const calls = runGeneratedCodeWithFakeMoexport(code);
  assert.deepEqual(calls, [canonicalJson(expected)]);
}

function runGeneratedCodeWithFakeMoexport(code: string): unknown[] {
  const tempDir = mkdtempSync(path.join(os.tmpdir(), "moexport-client-test-"));
  const packageDir = path.join(tempDir, "moexport");
  const callsPath = path.join(tempDir, "calls.jsonl");
  mkdirSync(packageDir);
  writeFileSync(
    path.join(packageDir, "__init__.py"),
    `
import json
from pathlib import Path

CALLS_PATH = Path(${JSON.stringify(callsPath)})

def _record(payload):
    with CALLS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, allow_nan=False) + "\\n")

class Result:
    bundle_path = "/tmp/export/bundles/sha256-demo"
    manifest_path = "/tmp/export/bundles/sha256-demo/manifest.json"
    invocation_path = "/tmp/export/bundles/sha256-demo/traces/invocation.json"
    invocation_index_path = "/tmp/export/bundles/sha256-demo/traces/index.json"
    manifest = {"id": "sha256-demo"}
    invocation = {"id": "invocation-demo"}

async def capture(spec, to=None):
    _record({"kind": "capture", "spec": spec, "to": to})
    return Result()
`,
  );
  writeFileSync(
    path.join(packageDir, "archive.py"),
    `
import base64
import json
from pathlib import Path

CALLS_PATH = Path(${JSON.stringify(callsPath)})

async def emit_bundle_archive(spec, *, marker):
    with CALLS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "archive", "spec": spec}, allow_nan=False) + "\\n")
    print(marker + base64.b64encode(b"zip-bytes").decode("ascii"))
`,
  );
  const script = `
import sys
import types
import asyncio
sys.modules["moexport"] = types.ModuleType("moexport")
sys.modules["moexport.archive"] = types.ModuleType("moexport.archive")

async def __run():
${indentPython(code)}

asyncio.run(__run())
`;
  try {
    const result = spawnSync(process.env.PYTHON ?? "python3", ["-c", script], {
      encoding: "utf8",
      env: {
        ...process.env,
        PYTHONPATH: [tempDir, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
      },
    });
    assert.equal(result.status, 0, result.stderr || result.stdout);
    return readFileSync(callsPath, "utf8")
      .trim()
      .split("\n")
      .filter(Boolean)
      .map((line) => JSON.parse(line) as unknown);
  } finally {
    rmSync(tempDir, { force: true, recursive: true });
  }
}

function indentPython(code: string): string {
  return code
    .split("\n")
    .map((line) => `    ${line}`)
    .join("\n");
}

function canonicalJson(value: unknown): unknown {
  return JSON.parse(JSON.stringify(value)) as unknown;
}

function readoutFormats(spec: Record<string, unknown>): unknown {
  const values = spec.values as Record<string, unknown>;
  const readout = values.readout as Record<string, unknown>;
  return readout.formats;
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
