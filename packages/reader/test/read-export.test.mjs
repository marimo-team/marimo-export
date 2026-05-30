import assert from "node:assert/strict";
import test from "node:test";

import { readExport, readExportIndex, validateExportManifest } from "../dist/index.js";

const manifest = {
  schema: "moexport.bundle.v1",
  version: 1,
  id: "sha256-test",
  sha256: "test",
  notebook: {
    name: "demo.py",
    source: null,
  },
  scenario_set: {
    id: "sha256-scenarios",
    sha256: "scenarios",
  },
  export: {
    id: "sha256-export",
    request_sha256: "export",
    target: "{'value': value}",
  },
  values: {
    value: {
      source: "value",
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
                  href: "blobs/sha256/aa/bb/aabb",
                  media_type: "application/json",
                  size: 2,
                  sha256: "aabb",
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

test("readExportIndex rejects index hrefs outside the hosted root", async () => {
  await assert.rejects(
    readExportIndex({
      root: "https://example.test/export/",
      index: "../index.json",
      fetch: unreachableFetch,
    }),
    /Invalid index href/,
  );
});

test("readExport rejects manifest hrefs outside the hosted root", async () => {
  await assert.rejects(
    readExport({
      root: "https://example.test/export/",
      manifest: "https://example.test/other/manifest.json",
      fetch: unreachableFetch,
    }),
    /Invalid bundle href/,
  );
});

test("readExport rejects manifest file hrefs outside the export root", async () => {
  await assert.rejects(
    readExport({
      root: "https://example.test/export/",
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

test("validateExportManifest rejects missing declared formats", () => {
  assert.throws(
    () =>
      validateExportManifest({
        ...manifest,
        values: {
          value: {
            ...manifest.values.value,
            formats: ["json", "text"],
          },
        },
      }),
    /must include declared format "text"/,
  );
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
