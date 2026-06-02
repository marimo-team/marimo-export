import assert from "node:assert/strict";
import test from "node:test";
import { readExport } from "@marimo-team/export-reader";
import {
  defaultJsonFormat,
  defaultScenario,
  defaultValue,
  fetchFixtureFile,
  hostedFiles,
  manifestWith,
} from "./fixtures/export-fixture.js";

test("readExport rejects the wrong manifest schema", async () => {
  await assert.rejects(
    readExport({
      root: "https://example.test/export/",
      fetch: fetchFixtureFile(
        hostedFiles(
          manifestWith((manifest) => {
            manifest.schema = "marimo.export.bundle.v1";
          }),
        ),
      ),
    }),
    /export manifest\.schema must be "moexport\.bundle\.v1"/,
  );
});

test("readExport rejects an entry outside the format files", async () => {
  await assert.rejects(
    readExport({
      root: "https://example.test/export/",
      fetch: fetchFixtureFile(
        hostedFiles(
          manifestWith((manifest) => {
            defaultJsonFormat(manifest).data.entry = "missing";
          }),
        ),
      ),
    }),
    /entry must name a file/,
  );
});

test("readExport rejects duplicate scenario ids", async () => {
  await assert.rejects(
    readExport({
      root: "https://example.test/export/",
      fetch: fetchFixtureFile(
        hostedFiles(
          manifestWith((manifest) => {
            const scenario = defaultScenario(manifest);
            manifest.scenarios = [scenario, scenario];
          }),
        ),
      ),
    }),
    /duplicate scenario "default"/,
  );
});

test("readExport rejects undeclared scenario values", async () => {
  await assert.rejects(
    readExport({
      root: "https://example.test/export/",
      fetch: fetchFixtureFile(
        hostedFiles(
          manifestWith((manifest) => {
            const scenario = defaultScenario(manifest);
            const value = scenario.values.value;
            assert.ok(value);
            scenario.values.extra = value;
          }),
        ),
      ),
    }),
    /contains undeclared value "extra"/,
  );
});

test("readExport rejects missing declared formats", async () => {
  await assert.rejects(
    readExport({
      root: "https://example.test/export/",
      fetch: fetchFixtureFile(
        hostedFiles(
          manifestWith((manifest) => {
            defaultValue(manifest).formats = ["json", "text"];
          }),
        ),
      ),
    }),
    /must include declared format "text"/,
  );
});
