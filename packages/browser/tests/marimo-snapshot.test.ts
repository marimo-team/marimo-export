import { describe, expect, test } from "vite-plus/test";

import { parseMarimoCellSnapshot, parseMarimoOutputSnapshot } from "../src/marimo-snapshot.js";
import { canonicalJson } from "../src/schema.js";
import type { JsonValue } from "../src/types.js";

const encoder = new TextEncoder();

describe("Marimo snapshot records", () => {
  test("rejects unknown cell channels", () => {
    const snapshot = outputSnapshot();
    snapshot.output = { channel: "unknown", data: "x", mimetype: "text/plain" };
    expect(() => parseMarimoOutputSnapshot(bytes(snapshot))).toThrow(/cell channel/u);
  });

  test("rejects file resources outside data URLs", () => {
    const snapshot = outputSnapshot();
    snapshot.resources.files = { module: "https://example.test/module.js" };
    expect(() => parseMarimoOutputSnapshot(bytes(snapshot))).toThrow(/data URL/u);
  });

  test("rejects malformed model lifecycle records", () => {
    const snapshot = outputSnapshot();
    snapshot.resources.modelNotifications = [
      {
        op: "model-lifecycle",
        model_id: `projection-${snapshot.projectionSha256}-model-0`,
        message: { method: "open" },
      },
    ];
    expect(() => parseMarimoOutputSnapshot(bytes(snapshot))).toThrow(/missing fields/u);
  });

  test("rejects model IDs owned by another projection", () => {
    const snapshot = outputSnapshot();
    snapshot.resources.modelNotifications = [
      {
        op: "model-lifecycle",
        model_id: `projection-${"b".repeat(64)}-model-0`,
        message: {
          method: "open",
          state: {},
          buffer_paths: [],
          buffers: [],
          esm_spec: null,
        },
      },
    ];
    expect(() => parseMarimoOutputSnapshot(bytes(snapshot))).toThrow(/snapshot projection/u);
  });

  test("requires each replay UI value to own a function namespace", () => {
    const snapshot = outputSnapshot();
    const objectId = `cell-1-projection-${"a".repeat(64)}-ui-control`;
    snapshot.resources.uiValues = { [objectId]: 3 };
    expect(() => parseMarimoOutputSnapshot(bytes(snapshot))).toThrow(/function namespace/u);
  });

  test("requires each function namespace to own a replay UI value", () => {
    const snapshot = outputSnapshot();
    const objectId = `cell-1-projection-${"a".repeat(64)}-ui-control`;
    snapshot.resources.functions = { [objectId]: [] };
    expect(() => parseMarimoOutputSnapshot(bytes(snapshot))).toThrow(/replay UI value/u);
  });

  test("rejects live Python functions in static replay resources", () => {
    const snapshot = outputSnapshot();
    const objectId = `cell-1-projection-${"a".repeat(64)}-ui-control`;
    snapshot.resources.functions = { [objectId]: ["validate"] };
    snapshot.resources.uiValues = { [objectId]: null };
    expect(() => parseMarimoOutputSnapshot(bytes(snapshot))).toThrow(/must be empty/u);
  });

  test("requires projection UI objects to match their snapshot owner", () => {
    const snapshot = outputSnapshot();
    const objectId = `other-cell-projection-${"a".repeat(64)}-ui-control`;
    snapshot.resources.functions = { [objectId]: [] };
    snapshot.resources.uiValues = { [objectId]: 3 };
    expect(() => parseMarimoOutputSnapshot(bytes(snapshot))).toThrow(/projection-scoped/u);
  });

  test("accepts a normalized embedded AnyWidget module path", () => {
    const snapshot = outputSnapshot();
    const modulePath = "/@file/widget.js";
    const modelUrl = "." + modulePath;
    snapshot.resources.files[modulePath] = "data:text/javascript;base64,ZXhwb3J0IGRlZmF1bHQge30=";
    snapshot.resources.modelNotifications = [
      {
        op: "model-lifecycle",
        model_id: `projection-${snapshot.projectionSha256}-model-0`,
        message: {
          method: "open",
          state: {},
          buffer_paths: [],
          buffers: [],
          esm_spec: { url: modelUrl, hash: "widget-hash" },
        },
      },
    ];

    const parsed = parseMarimoOutputSnapshot(bytes(snapshot));

    expect(parsed.resources.files[modulePath]).toBe(
      "data:text/javascript;base64,ZXhwb3J0IGRlZmF1bHQge30=",
    );
    expect(parsed.resources.modelNotifications[0]).toMatchObject({
      model_id: `projection-${snapshot.projectionSha256}-model-0`,
      message: { esm_spec: { url: modelUrl } },
    });
    const notification = parsed.resources.modelNotifications[0]!;
    expect(Object.isFrozen(notification)).toBe(true);
    expect(Object.isFrozen(notification.message)).toBe(true);
    if (notification.message.method !== "open") throw new Error("Expected an open message.");
    expect(notification.message.buffer_paths).toEqual([]);
    expect(notification.message.esm_spec).toEqual({ url: modelUrl, hash: "widget-hash" });
  });

  test("preserves projection-scoped nested form references", () => {
    const snapshot = outputSnapshot();
    const prefix = `${snapshot.ownerCellId}-projection-${snapshot.projectionSha256}-ui-`;
    const formId = `${prefix}cell-1-3`;
    const dictionaryId = `${prefix}cell-1-2`;
    const regionId = `${prefix}cell-1-1`;
    snapshot.output = {
      channel: "output",
      mimetype: "text/html",
      data:
        `<marimo-ui-element object-id="${formId}">` +
        `<marimo-form data-element-id='"${dictionaryId}"'>` +
        `<marimo-dict data-element-ids='{"${regionId}":"region"}'>` +
        `<marimo-json-output data-json-data='{"region":"text/html:<marimo-ui-element object-id=${regionId}></marimo-ui-element>"}'>` +
        "</marimo-json-output></marimo-dict></marimo-form></marimo-ui-element>",
    };
    snapshot.resources.functions = { [formId]: [], [dictionaryId]: [], [regionId]: [] };
    snapshot.resources.uiValues = {
      [formId]: null,
      [dictionaryId]: { region: ["Europe"] },
      [regionId]: ["Europe"],
    };

    const parsed = parseMarimoOutputSnapshot(bytes(snapshot));

    expect(parsed.output?.data).toContain(formId);
    expect(parsed.output?.data).toContain(dictionaryId);
    expect(parsed.output?.data).toContain(regionId);
  });

  test("merges independent projection-owned widget model namespaces", () => {
    const snapshots = ["a".repeat(64), "b".repeat(64)].map((projection) => {
      const snapshot = outputSnapshot();
      snapshot.projectionSha256 = projection;
      const modelId = `projection-${projection}-model-0`;
      snapshot.output = {
        channel: "output",
        mimetype: "text/html",
        data: `<marimo-anywidget data-model-id='&quot;${modelId}&quot;'></marimo-anywidget>`,
      };
      snapshot.resources.modelNotifications = [
        {
          op: "model-lifecycle",
          model_id: modelId,
          message: {
            method: "open",
            state: {},
            buffer_paths: [],
            buffers: [],
            esm_spec: {
              url: "data:text/javascript;base64,ZXhwb3J0IGRlZmF1bHQge30=",
              hash: `hash-${projection}`,
            },
          },
        },
      ];
      return parseMarimoOutputSnapshot(bytes(snapshot));
    });
    const models = new Map<string, object>();
    for (const snapshot of snapshots) {
      for (const notification of snapshot.resources.modelNotifications) {
        expect(models.has(notification.model_id)).toBe(false);
        models.set(notification.model_id, notification);
      }
    }

    expect(models.size).toBe(2);
  });

  test("merges projection-scoped UI graphs with shared input ownership", () => {
    const outputProjection = "a".repeat(64);
    const cellProjection = "b".repeat(64);
    const outputObjectId = `cell-controls-projection-${outputProjection}-ui-cell-controls-0`;
    const cellObjectId = `cell-controls-projection-${cellProjection}-ui-cell-controls-0`;
    const outputModelId = `projection-${outputProjection}-model-0`;
    const cellModelId = `projection-${cellProjection}-model-0`;
    const output = outputSnapshot();
    output.projectionSha256 = outputProjection;
    output.ownerCellId = "cell-controls";
    output.output = {
      channel: "output",
      mimetype: "text/html",
      data: `<marimo-ui-element object-id="${outputObjectId}"></marimo-ui-element>`,
    };
    output.resources.functions = { [outputObjectId]: [] };
    output.resources.uiValues = { [outputObjectId]: { model_id: outputModelId } };
    const cell = {
      schema: "marimo.cell.v1",
      projectionSha256: cellProjection,
      cell: {
        id: "cell-controls",
        name: "controls",
        codeSha256: "c".repeat(64),
        config: {},
      },
      outcome: "completed",
      output: {
        channel: "output",
        mimetype: "text/html",
        data: `<marimo-ui-element object-id="${cellObjectId}"></marimo-ui-element>`,
      },
      console: [],
      resources: {
        files: {},
        functions: { [cellObjectId]: [] },
        modelNotifications: [],
        uiValues: { [cellObjectId]: { model_id: cellModelId } },
      },
    };
    const resources = [
      parseMarimoOutputSnapshot(bytes(output)).resources,
      parseMarimoCellSnapshot(bytes(cell)).resources,
    ];
    const uiValues = new Map<string, unknown>();
    for (const item of resources) {
      for (const [objectId, value] of Object.entries(item.uiValues)) {
        expect(uiValues.has(objectId)).toBe(false);
        uiValues.set(objectId, value);
      }
    }
    const controlBindings = {
      [outputObjectId]: { input: "scale", path: [] },
      [cellObjectId]: { input: "scale", path: [] },
    };

    expect(uiValues.size).toBe(2);
    expect([...uiValues.keys()].map((objectId) => controlBindings[objectId]!.input)).toEqual([
      "scale",
      "scale",
    ]);
  });
});

interface MutableOutputSnapshot {
  schema: string;
  projectionSha256: string;
  ownerCellId: string;
  output: Record<string, unknown> | null;
  resources: {
    files: Record<string, string>;
    functions: Record<string, string[]>;
    modelNotifications: unknown[];
    uiValues: Record<string, unknown>;
  };
}

function outputSnapshot(): MutableOutputSnapshot {
  return {
    schema: "marimo.output.v1",
    projectionSha256: "a".repeat(64),
    ownerCellId: "cell-1",
    output: null,
    resources: { files: {}, functions: {}, modelNotifications: [], uiValues: {} },
  };
}

function bytes(value: unknown): Uint8Array {
  return encoder.encode(canonicalJson(value as JsonValue));
}
