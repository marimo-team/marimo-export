import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { parseExportSpec, type ExportSpec } from "@marimo-team/export-client";

test("frameworkless server archive module builds a valid public export spec", async () => {
  const specModuleUrl = pathToFileURL(
    path.resolve(process.cwd(), "../../examples/frameworkless/server-archive-spec.mjs"),
  ).href;
  const specModule = (await import(specModuleUrl)) as {
    createQueueingArchiveSpec(parseSpec: typeof parseExportSpec): ExportSpec;
  };

  const spec = parseExportSpec(specModule.createQueueingArchiveSpec(parseExportSpec));
  const roundTripped = parseExportSpec(JSON.parse(JSON.stringify(spec)) as unknown);

  assert.deepEqual(JSON.parse(JSON.stringify(roundTripped)), JSON.parse(JSON.stringify(spec)));
  assert.deepEqual(spec.scenarios?.[0]?.state?.arrival_rate, { code: "6.0" });
  assert.deepEqual(spec.values.summary?.source, { def: "queue_summary" });
});
