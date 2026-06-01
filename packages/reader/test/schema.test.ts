import assert from "node:assert/strict";
import test from "node:test";
import { validateExportManifest } from "@marimo-team/export-reader";
import {
  defaultJsonArtifact,
  defaultScenario,
  defaultValue,
  manifestWith,
} from "./fixtures/export-fixture.js";

test("validateExportManifest rejects the wrong schema", () => {
  assert.throws(() => {
    validateExportManifest(
      manifestWith((manifest) => {
        manifest.schema = "marimo.export.bundle.v1";
      }),
    );
  }, /export manifest\.schema must be "moexport\.bundle\.v1"/);
});

test("validateExportManifest rejects an entry outside the artifact files", () => {
  assert.throws(() => {
    validateExportManifest(
      manifestWith((manifest) => {
        defaultJsonArtifact(manifest).data.entry = "missing";
      }),
    );
  }, /entry must name a file/);
});

test("validateExportManifest rejects duplicate scenario ids", () => {
  assert.throws(() => {
    validateExportManifest(
      manifestWith((manifest) => {
        const scenario = defaultScenario(manifest);
        manifest.scenarios = [scenario, scenario];
      }),
    );
  }, /duplicate scenario "default"/);
});

test("validateExportManifest rejects undeclared scenario values", () => {
  assert.throws(() => {
    validateExportManifest(
      manifestWith((manifest) => {
        const scenario = defaultScenario(manifest);
        const value = scenario.values.value;
        assert.ok(value);
        scenario.values.extra = value;
      }),
    );
  }, /contains undeclared value "extra"/);
});

test("validateExportManifest rejects missing declared artifacts", () => {
  assert.throws(() => {
    validateExportManifest(
      manifestWith((manifest) => {
        defaultValue(manifest).artifacts = ["json", "text"];
      }),
    );
  }, /must include declared artifact "text"/);
});
