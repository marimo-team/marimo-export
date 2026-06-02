import { zipSync } from "fflate";
import assert from "node:assert/strict";
import type {
  FormatRecord,
  BlobRef,
  ExportManifest,
  ExportRootIndex,
  FetchLike,
  LocalReadFile,
  ManifestScenario,
  ManifestValue,
} from "../../src/types.js";

export const jsonPayload = '{"ok":true}';
export const jsonPayloadSha = "4062edaf750fb8074e7e83e0c9028c94e32468a8b6f1614774328ef045150f93";
export const jsonPayloadHref = `blobs/sha256/40/62/${jsonPayloadSha}`;

const baseManifest = {
  schema: "moexport.bundle.v1",
  version: 1,
  id: "sha256-test",
  sha256: "test",
  notebook: {
    name: "demo.py",
    source: null,
    source_sha256: "notebook-sha",
  },
  scenario_set: {
    id: "sha256-scenarios",
    sha256: "scenarios",
  },
  capture: {
    id: "sha256-export",
    request_sha256: "export",
  },
  values: {
    value: {
      source: { type: "definition", name: "value" },
      formats: ["json"],
    },
  },
  scenarios: [
    {
      id: "default",
      state: {},
      values: {
        value: {
          json: {
            format_id: "json.v1",
            media_type: "application/json",
            data: {
              type: "bundle",
              files: {
                data: {
                  href: jsonPayloadHref,
                  media_type: "application/json",
                  size: jsonPayload.length,
                  sha256: jsonPayloadSha,
                },
              },
              entry: "data",
            },
            metadata: null,
          },
        },
      },
    },
  ],
} satisfies ExportManifest;

export type ExportFixtureFile = string | ExportManifest | ExportRootIndex;

export function validManifest(): ExportManifest {
  return structuredClone(baseManifest) as ExportManifest;
}

export function manifestWith(mutator: (manifest: ExportManifest) => void): ExportManifest {
  const manifest = validManifest();
  mutator(manifest);
  return manifest;
}

export function rootIndexFor(
  manifest: ExportManifest = validManifest(),
  bundles: ExportManifest[] = [manifest],
): ExportRootIndex {
  return {
    schema: "moexport.root_index.v1",
    version: 1,
    latest: rootBundle(manifest),
    bundles: bundles.map(rootBundle),
  };
}

function rootBundle(manifest: ExportManifest): ExportRootIndex["bundles"][number] {
  return {
    id: manifest.id,
    sha256: manifest.sha256,
    manifest_href: `bundles/${manifest.id}/manifest.json`,
    updated_at: "2026-06-01T00:00:00Z",
    latest_invocation_href: `bundles/${manifest.id}/traces/sha256-trace.json`,
  };
}

export function directoryFiles(
  manifest: ExportManifest = validManifest(),
): Record<string, ExportFixtureFile> {
  return {
    "export/index.json": rootIndexFor(manifest),
    [`export/bundles/${manifest.id}/manifest.json`]: manifest,
    [`export/${jsonPayloadHref}`]: jsonPayload,
  };
}

export function hostedFiles(
  manifest: ExportManifest = validManifest(),
): Record<string, ExportFixtureFile> {
  return {
    "https://example.test/export/index.json": rootIndexFor(manifest),
    [`https://example.test/export/bundles/${manifest.id}/manifest.json`]: manifest,
    [`https://example.test/export/${jsonPayloadHref}`]: jsonPayload,
  };
}

export function archiveBytes(
  manifest: ExportManifest = validManifest(),
  files: Record<string, ExportFixtureFile> = archiveFiles(manifest),
): Uint8Array {
  const entries: Record<string, Uint8Array> = {};
  for (const [path, content] of Object.entries(files)) {
    entries[path] = new TextEncoder().encode(serializeFixtureFile(content));
  }
  return zipSync(entries);
}

export function archiveFiles(
  manifest: ExportManifest = validManifest(),
): Record<string, ExportFixtureFile> {
  return {
    "index.json": rootIndexFor(manifest),
    [`bundles/${manifest.id}/manifest.json`]: manifest,
    [jsonPayloadHref]: jsonPayload,
  };
}

export function readFixtureFile(files: Record<string, ExportFixtureFile>): LocalReadFile {
  return async (path) => {
    const content = files[path];
    if (content === undefined) {
      throw new Error(`missing ${path}`);
    }
    return serializeFixtureFile(content);
  };
}

export function fetchFixtureFile(files: Record<string, ExportFixtureFile>): FetchLike {
  return async (input) => {
    const url = String(input);
    const content = files[url];
    if (content === undefined) {
      return new Response("not found", {
        status: 404,
        statusText: "Not Found",
      });
    }

    if (typeof content === "string") {
      return new Response(content);
    }

    return Response.json(content);
  };
}

export function defaultScenario(manifest: ExportManifest): ManifestScenario {
  const scenario = manifest.scenarios[0];
  assert.ok(scenario);
  return scenario;
}

export function defaultValue(manifest: ExportManifest): ManifestValue {
  const value = manifest.values.value;
  assert.ok(value);
  return value;
}

export function defaultJsonFormat(manifest: ExportManifest): FormatRecord {
  const formats = defaultScenario(manifest).values.value;
  assert.ok(formats);
  const format = formats.json;
  assert.ok(format);
  return format;
}

export function dataFile(format: FormatRecord): BlobRef {
  const file = format.data.files.data;
  assert.ok(file);
  return file;
}

export const unreachableFetch: FetchLike = async (input) => {
  throw new Error(`fetch should not be called for ${String(input)}`);
};

function serializeFixtureFile(content: ExportFixtureFile): string {
  return typeof content === "string" ? content : JSON.stringify(content);
}
