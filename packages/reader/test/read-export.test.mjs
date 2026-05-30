import assert from "node:assert/strict";
import test from "node:test";

import { readExport, readExportIndex } from "../dist/index.js";

const manifest = {
  schema: "marimo.export.bundle.v1",
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

test("artifact URLs reject hosted file hrefs outside the export root", async () => {
  const exp = await readExport({
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
  });

  const handle = exp.get({
    scenario: "default",
    value: "value",
    format: "json",
  });

  assert.throws(() => handle.url(), /Invalid bundle href/);
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
