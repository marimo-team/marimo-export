import { describe, expect, test } from "vite-plus/test";

import {
  mergeMarimoReplayResources,
  type MarimoModelLifecycleNotification,
  type MarimoOutputSnapshot,
} from "../src/index.js";

const digest = "a".repeat(64);
const parent = `projection-${digest}-model-0`;
const child = `projection-${digest}-model-1`;

const notification = (
  id: string,
  message: MarimoModelLifecycleNotification["message"],
): MarimoModelLifecycleNotification => ({ op: "model-lifecycle", model_id: id, message });

const opened = (id: string) =>
  notification(id, {
    method: "open",
    state: {},
    buffer_paths: [],
    buffers: [],
    esm_spec: null,
  });

const snapshot = (
  notifications: readonly MarimoModelLifecycleNotification[] = [],
  overrides: Partial<MarimoOutputSnapshot["resources"]> = {},
): MarimoOutputSnapshot => ({
  schema: "marimo.output.v1",
  ownerCellId: "summary",
  projectionSha256: digest,
  output: null,
  resources: {
    files: {},
    functions: {},
    uiValues: {},
    modelNotifications: notifications,
    ...overrides,
  },
});

describe("replay resource composition", () => {
  test("preserves repeated events and cross-model order across overlapping snapshots", () => {
    const parentOpen = opened(parent);
    const childOpen = opened(child);
    const update = notification(parent, { method: "custom", content: { child }, buffers: [] });
    const result = mergeMarimoReplayResources([
      snapshot([parentOpen, update, update]),
      snapshot([parentOpen, childOpen, update, update]),
      snapshot([parentOpen, childOpen, update, update]),
    ]);

    expect(result.modelNotifications).toEqual([parentOpen, childOpen, update, update]);
    expect(Object.isFrozen(result.modelNotifications)).toBe(true);
  });

  test("rejects contradictory cross-model ordering", () => {
    expect(() =>
      mergeMarimoReplayResources([
        snapshot([opened(parent), opened(child)]),
        snapshot([opened(child), opened(parent)]),
      ]),
    ).toThrow(/conflicting model lifecycle order/u);
  });

  test("rejects conflicting complete lifecycle sequences", () => {
    expect(() =>
      mergeMarimoReplayResources([
        snapshot([opened(parent)]),
        snapshot([opened(parent), notification(parent, { method: "close" })]),
      ]),
    ).toThrow(/conflicting lifecycle records/u);
  });

  test("deduplicates resources while preserving reserved property names", () => {
    const objectId = `summary-projection-${digest}-ui-control`;
    const resources = {
      files: Object.fromEntries([["__proto__", "data:text/plain,resource"]]),
      functions: { [objectId]: [] },
      uiValues: { [objectId]: { amount: 3, mode: "ready" } },
    };
    const result = mergeMarimoReplayResources([
      snapshot([], resources),
      snapshot([], { ...resources, uiValues: { [objectId]: { mode: "ready", amount: 3 } } }),
    ]);
    expect(Object.getOwnPropertyDescriptor(result.files, "__proto__")?.value).toBe(
      "data:text/plain,resource",
    );
    expect(result.uiValues).toEqual({ [objectId]: { amount: 3, mode: "ready" } });
    expect(Object.isFrozen(result.uiValues[objectId])).toBe(true);
  });

  test("rejects conflicting files and control values", () => {
    expect(() =>
      mergeMarimoReplayResources([
        snapshot([], { files: { chart: "data:text/plain,a" } }),
        snapshot([], { files: { chart: "data:text/plain,b" } }),
      ]),
    ).toThrow(/conflicting contents/u);
    const objectId = `summary-projection-${digest}-ui-control`;
    expect(() =>
      mergeMarimoReplayResources(
        [1, 2].map((value) =>
          snapshot([], {
            functions: { [objectId]: [] },
            uiValues: { [objectId]: value },
          }),
        ),
      ),
    ).toThrow(/conflicting UI values/u);
  });

  test("composes resource closures containing several large valid control values", () => {
    const first = `summary-projection-${digest}-ui-first`;
    const second = `summary-projection-${digest}-ui-second`;
    const values = Array.from({ length: 55_000 }, (_, index) => index);
    const result = mergeMarimoReplayResources([
      snapshot([], {
        functions: { [first]: [], [second]: [] },
        uiValues: { [first]: values, [second]: values },
      }),
    ]);
    expect(result.uiValues[first]).toEqual(values);
    expect(result.uiValues[second]).toEqual(values);
  });

  test("validates replay ownership and function capability before composition", () => {
    expect(() =>
      mergeMarimoReplayResources([
        snapshot([], {
          functions: { unscoped: [] },
          uiValues: { unscoped: null },
        }),
      ]),
    ).toThrow(/projection-scoped/u);
    const objectId = `summary-projection-${digest}-ui-control`;
    expect(() =>
      mergeMarimoReplayResources([
        snapshot([], {
          functions: { [objectId]: ["validate"] },
          uiValues: { [objectId]: null },
        }),
      ]),
    ).toThrow(/must be empty for static replay/u);
  });
});
