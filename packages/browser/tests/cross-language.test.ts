import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vite-plus/test";

import {
  openExport,
  parseMarimoCellSnapshot,
  parseMarimoOutputSnapshot,
  scalarLoader,
} from "../src/index.js";
import { canonicalJson } from "../src/schema.js";
import { isRecordValue } from "../src/value-types.js";
import type { MutableJsonObject, MutableJsonValue } from "./fixture.js";
import { exportFixture, mutableArray, mutableObject } from "./fixture.js";
import canonicalCases from "../../../tests/fixtures/canonical-json/cases.json" with { type: "json" };
import httpModuleUrlCases from "../../../tests/fixtures/export/http-module-urls.json" with { type: "json" };
import indexLimits from "../../../tests/fixtures/export/index-limits.json" with { type: "json" };
import inputNameCases from "../../../tests/fixtures/export/input-names.json" with { type: "json" };
import malformedProjectionRecords from "../../../tests/fixtures/export/malformed-projection-records.json" with { type: "json" };
import projectionRecords from "../../../tests/fixtures/export/projection-records.json" with { type: "json" };

const scalarIndex = fileURLToPath(
  new URL("../../../tests/fixtures/export/scalar-index.json", import.meta.url),
);
const fixtureUiObjectId =
  "cell-summary-projection-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-ui-cell-summary-ui";

interface HttpModuleUrlCase {
  readonly name: string;
  readonly url: string;
  readonly valid: boolean;
}

describe("Python and TypeScript protocol fixtures", () => {
  test.each(canonicalCases)("$name canonicalizes to the Python bytes", ({ value, canonical }) => {
    expect(canonicalJson(value)).toBe(canonical);
  });

  test.each(inputNameCases)(
    "$name matches the durable input-name policy",
    async ({ value, valid }) => {
      const fixture = await exportFixture({ inputs: [value, "width"] });
      const opened = openExport("https://example.test/stocks", { fetch: fixture.fetch });
      if (valid) {
        await expect(opened).resolves.toMatchObject({ inputNames: [value, "width"] });
      } else {
        await expect(opened).rejects.toMatchObject({ code: "export_invalid" });
      }
    },
  );

  test("opens a canonical ExportIndex emitted by Python", async () => {
    const index = (await readFile(scalarIndex, "utf8")).trimEnd();
    const notebookExport = await openExport("https://example.test/fixture", {
      fetch: async () => new Response(index),
    });

    expect(notebookExport.states().map((state) => state.fingerprint)).toEqual(
      notebookExport
        .states()
        .map((state) => state.fingerprint)
        .sort(),
    );
    expect(notebookExport.state("one").aliases).toEqual(["one"]);
    expect(notebookExport.specSha256).toBe("d".repeat(64));
    expect(notebookExport.defaultState).toBe(notebookExport.state("one"));
    expect(notebookExport.controlBindings).toEqual({
      "choice-control": {
        input: "choice",
        path: [],
        domain: { kind: "options", values: ["A", "B"] },
      },
    });
    await expect(notebookExport.state("one").output("answer").load(scalarLoader())).resolves.toBe(
      42,
    );
    await expect(notebookExport.state("two").output("answer").load(scalarLoader())).resolves.toBe(
      9007199254740992n,
    );
  });

  test("opens an index at the shared value limit and rejects one more value", async () => {
    const atLimit = await paddedExportFixture(indexLimits.max_index_values);
    const overLimit = await paddedExportFixture(indexLimits.max_index_values + 1);

    const opened = await openExport("https://example.test/stocks", { fetch: atLimit.fetch });
    expect(opened.states()).toHaveLength(2);
    await expect(
      openExport("https://example.test/stocks", { fetch: overLimit.fetch }),
    ).rejects.toMatchObject({ code: "export_invalid" });
  });

  test("decodes the Python projection record fixtures", () => {
    const output = parseMarimoOutputSnapshot(
      new TextEncoder().encode(canonicalJson(projectionRecords.output)),
    );
    const cell = parseMarimoCellSnapshot(
      new TextEncoder().encode(canonicalJson(projectionRecords.cell)),
    );

    expect(output.ownerCellId).toBe("cell-summary");
    expect(output.resources.functions[fixtureUiObjectId]).toEqual([]);
    expect(output.resources.uiValues[fixtureUiObjectId]).toBe(3);
    expect(output.output?.data).toContain(`object-id="${fixtureUiObjectId}"`);
    expect(cell.cell.name).toBe("summary");
    expect(cell.console[0]?.data).toBe("ready\n");
    expect(canonicalJson(projectionRecords.json)).toBe(
      '{"ready":true,"rows":[{"label":"alpha","value":1},{"label":"beta","value":2}]}',
    );
  });

  test.each(fixtureCases<HttpModuleUrlCase>(httpModuleUrlCases))(
    "$name at the Marimo snapshot boundary",
    ({ url, valid }) => {
      const record: MutableJsonValue = structuredClone(projectionRecords.output);
      applyMutation(record, {
        name: "HTTP module URL",
        operation: "set",
        path: ["resources", "modelNotifications", 1, "message", "esm_spec", "url"],
        record: "output",
        value: url,
      });
      const parse = () =>
        parseMarimoOutputSnapshot(new TextEncoder().encode(canonicalJson(record)));
      if (valid) {
        expect(parse).not.toThrow();
      } else {
        expect(parse).toThrow();
      }
    },
  );

  test.each(fixtureCases<ProjectionMutation>(malformedProjectionRecords))(
    "rejects malformed projection case: $name",
    (testCase) => {
      const record: MutableJsonValue = structuredClone(projectionRecords[testCase.record]);
      applyMutation(record, testCase);
      const bytes = new TextEncoder().encode(canonicalJson(record));
      const parse =
        testCase.record === "output" ? parseMarimoOutputSnapshot : parseMarimoCellSnapshot;

      expect(() => parse(bytes)).toThrow();
    },
  );
});

const PAD_OUTPUTS = 11;
const PAD_VALUES = 99_000;

// Fill JSON outputs with zeros until the index holds exactly `values` JSON
// values. One JSON output value stays below the 100,000-value portable limit.
async function paddedExportFixture(values: number) {
  return exportFixture({
    indexTransform(index) {
      const outputs = mutableArray(index.outputs, "index.outputs");
      const pads: MutableJsonValue[][] = [];
      for (let position = 0; position < PAD_OUTPUTS; position += 1) {
        outputs.push(`pad_${position}`);
      }
      for (const state of Object.values(mutableObject(index.states, "index.states"))) {
        const stateOutputs = mutableObject(mutableObject(state, "state").outputs, "outputs");
        for (let position = 0; position < PAD_OUTPUTS; position += 1) {
          const pad: MutableJsonValue[] = [];
          pads.push(pad);
          stateOutputs[`pad_${position}`] = jsonDescriptor(pad);
        }
      }
      let missing = values - jsonValueCount(index);
      for (const pad of pads) {
        const added = Math.min(missing, PAD_VALUES);
        pad.push(...Array.from({ length: added }, () => 0));
        missing -= added;
      }
      if (missing !== 0) throw new Error("Padded fixture cannot reach the requested value count.");
    },
  });
}

function jsonDescriptor(value: MutableJsonValue): MutableJsonObject {
  return {
    codec: "marimo.json.v1",
    media_type: "application/vnd.marimo.json.v1+json",
    provenance: { python_type: "builtins.list" },
    value,
  };
}

function jsonValueCount(value: MutableJsonValue): number {
  if (Array.isArray(value)) {
    return value.reduce<number>((count, item) => count + jsonValueCount(item), 1);
  }
  if (isRecordValue(value)) {
    return Object.values(value).reduce<number>(
      (count, item) => count + 1 + jsonValueCount(item),
      1,
    );
  }
  return 1;
}

interface ProjectionMutation {
  readonly name: string;
  readonly operation: "delete" | "set";
  readonly path: readonly (number | string)[];
  readonly record: "cell" | "output";
  readonly value?: MutableJsonValue;
}

const applyMutation = (record: MutableJsonValue, mutation: ProjectionMutation): void => {
  let parent = record;
  for (const token of mutation.path.slice(0, -1)) {
    parent = mutationChild(parent, token);
  }
  const token = mutation.path.at(-1);
  if (token === undefined) throw new Error("Malformed fixture path must not be empty.");
  if (Array.isArray(parent)) {
    if (!Number.isSafeInteger(token)) throw new Error("Array fixture paths require an index.");
    if (mutation.operation === "delete") {
      Reflect.deleteProperty(parent, Number(token));
      return;
    }
    if (mutation.value === undefined) throw new Error("Set mutations require a value.");
    parent[Number(token)] = structuredClone(mutation.value);
    return;
  }
  const object = mutableObject(parent, "mutation parent");
  const key = String(token);
  if (mutation.operation === "delete") {
    delete object[key];
    return;
  }
  if (mutation.value === undefined) throw new Error("Set mutations require a value.");
  object[key] = structuredClone(mutation.value);
};

function mutationChild(parent: MutableJsonValue, token: number | string): MutableJsonValue {
  if (Array.isArray(parent)) {
    if (!Number.isSafeInteger(token)) throw new Error("Array fixture paths require an index.");
    const child = parent[Number(token)];
    if (child === undefined) throw new Error(`Malformed fixture path stops before ${token}.`);
    return child;
  }
  const object = mutableObject(parent, "mutation path");
  const child = object[String(token)];
  if (child === undefined) throw new Error(`Malformed fixture path stops before ${token}.`);
  return child;
}

function fixtureCases<Case, Value = object>(value: Value): readonly Case[] {
  // SAFETY: Checked-in cross-language fixtures are validated by the Python fixture generator.
  return value as readonly Case[];
}
