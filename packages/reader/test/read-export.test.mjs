import assert from "node:assert/strict";
import test from "node:test";

import { exportDirectory, exportRoot, openExport, validateExportManifest } from "../dist/index.js";

const jsonPayload = '{"ok":true}';
const jsonPayloadSha = "4062edaf750fb8074e7e83e0c9028c94e32468a8b6f1614774328ef045150f93";
const jsonPayloadHref = `blobs/sha256/40/62/${jsonPayloadSha}`;

const manifest = {
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
      artifacts: ["json"],
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
};

test("openExport rejects index hrefs outside the hosted root", async () => {
  await assert.rejects(
    openExport(
      exportRoot("https://example.test/export/", {
        index: "../index.json",
        fetch: unreachableFetch,
      }),
    ),
    /Invalid index href/,
  );
});

test("openExport rejects manifest hrefs outside the hosted root", async () => {
  await assert.rejects(
    openExport(
      exportRoot("https://example.test/export/", {
        manifest: "https://example.test/other/manifest.json",
        fetch: unreachableFetch,
      }),
    ),
    /Invalid bundle href/,
  );
});

test("openExport rejects manifest file hrefs outside the export root", async () => {
  await assert.rejects(
    openExport(
      exportRoot("https://example.test/export/", {
        manifest: "bundles/sha256-test/manifest.json",
        fetch: jsonFetch({
          "https://example.test/export/bundles/sha256-test/manifest.json": {
            ...manifest,
            scenarios: [
              {
                ...manifest.scenarios[0],
                values: {
                  value: {
                    json: {
                      ...manifest.scenarios[0].values.value.json,
                      data: {
                        ...manifest.scenarios[0].values.value.json.data,
                        files: {
                          data: {
                            ...manifest.scenarios[0].values.value.json.data.files.data,
                            href: "../secret.json",
                          },
                        },
                      },
                    },
                  },
                },
              },
            ],
          },
        }),
      }),
    ),
    /Invalid export manifest\.scenarios\[0\]\.values\.value\.json\.data\.files\.data\.href/,
  );
});

test("validateExportManifest rejects the wrong schema", () => {
  assert.throws(
    () =>
      validateExportManifest({
        ...manifest,
        schema: "marimo.export.bundle.v1",
      }),
    /export manifest\.schema must be "moexport\.bundle\.v1"/,
  );
});

test("validateExportManifest rejects an entry outside the artifact files", () => {
  assert.throws(
    () =>
      validateExportManifest({
        ...manifest,
        scenarios: [
          {
            ...manifest.scenarios[0],
            values: {
              value: {
                json: {
                  ...manifest.scenarios[0].values.value.json,
                  data: {
                    ...manifest.scenarios[0].values.value.json.data,
                    entry: "missing",
                  },
                },
              },
            },
          },
        ],
      }),
    /entry must name a file/,
  );
});

test("validateExportManifest rejects duplicate scenario ids", () => {
  assert.throws(
    () =>
      validateExportManifest({
        ...manifest,
        scenarios: [manifest.scenarios[0], manifest.scenarios[0]],
      }),
    /duplicate scenario "default"/,
  );
});

test("validateExportManifest rejects undeclared scenario values", () => {
  assert.throws(
    () =>
      validateExportManifest({
        ...manifest,
        scenarios: [
          {
            ...manifest.scenarios[0],
            values: {
              ...manifest.scenarios[0].values,
              extra: manifest.scenarios[0].values.value,
            },
          },
        ],
      }),
    /contains undeclared value "extra"/,
  );
});

test("validateExportManifest rejects missing declared artifacts", () => {
  assert.throws(
    () =>
      validateExportManifest({
        ...manifest,
        values: {
          value: {
            ...manifest.values.value,
            artifacts: ["json", "text"],
          },
        },
      }),
    /must include declared artifact "text"/,
  );
});

test("openExport opens a bundle through a file reader", async () => {
  const files = {
    "export/index.json": {
      schema: "moexport.root_index.v1",
      version: 1,
      latest: {
        id: "sha256-test",
        sha256: "test",
        manifest_href: "bundles/sha256-test/manifest.json",
        updated_at: "2026-06-01T00:00:00Z",
        latest_invocation_href: "bundles/sha256-test/traces/sha256-trace.json",
      },
      bundles: [],
    },
    "export/bundles/sha256-test/manifest.json": manifest,
    [`export/${jsonPayloadHref}`]: jsonPayload,
  };

  const exp = await openExport(
    exportDirectory("export", {
      readFile: async (file) => {
        if (!(file in files)) {
          throw new Error(`missing ${file}`);
        }
        if (typeof files[file] === "string") {
          return files[file];
        }
        return JSON.stringify(files[file]);
      },
      url: (href) => `/export/${href}`,
    }),
  );

  const handle = exp.artifact({ scenario: "default", value: "value", artifact: "json" });

  assert.equal(handle.url(), `/export/${jsonPayloadHref}`);
  assert.deepEqual(await handle.json(), { ok: true });
  assert.equal(exp.scenario("default").id, "default");
  assert.deepEqual(
    exp.scenarioRecords().map((scenario) => scenario.id),
    ["default"],
  );
});

test("artifact reads reject bytes that do not match the manifest digest", async () => {
  const exp = await openExport(
    exportDirectory("export", {
      readFile: async (file) => {
        if (file.endsWith("index.json")) {
          return JSON.stringify({
            schema: "moexport.root_index.v1",
            version: 1,
            latest: {
              id: "sha256-test",
              sha256: "test",
              manifest_href: "bundles/sha256-test/manifest.json",
              updated_at: "2026-06-01T00:00:00Z",
              latest_invocation_href: "bundles/sha256-test/traces/sha256-trace.json",
            },
            bundles: [],
          });
        }
        if (file.endsWith("manifest.json")) {
          const badManifest = structuredClone(manifest);
          badManifest.scenarios[0].values.value.json.data.files.data.sha256 = "0".repeat(64);
          return JSON.stringify(badManifest);
        }
        return jsonPayload;
      },
    }),
  );

  const handle = exp.artifact({ scenario: "default", value: "value", artifact: "json" });
  await assert.rejects(handle.bytes(), /SHA-256/);
});

function jsonFetch(files) {
  return async (input) => {
    const url = String(input);
    if (!(url in files)) {
      return new Response("not found", {
        status: 404,
        statusText: "Not Found",
      });
    }

    return Response.json(files[url]);
  };
}

async function unreachableFetch(input) {
  throw new Error(`fetch should not be called for ${String(input)}`);
}
