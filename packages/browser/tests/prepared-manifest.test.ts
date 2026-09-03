import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { expect, test } from "vite-plus/test";

import { parsePreparedExportManifest } from "../src/prepared/index.js";

const fixturePath = fileURLToPath(
  new URL("../../../tests/fixtures/export/prepared-manifest.json", import.meta.url),
);

test("parses the canonical Python prepared manifest fixture", async () => {
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  const manifest = parsePreparedExportManifest(fixture);

  expect(manifest).toEqual({
    schema: "marimo-export.prepared.v1",
    instance: "1".repeat(64),
    exportUrl: "./publication/",
    inputs: { choice: "A" },
    stateFingerprint: "2".repeat(64),
    refreshIntervalMs: 1000,
  });
});
