import { expect, expectTypeOf, test, vi } from "vite-plus/test";

import {
  defineBlobAssetLoader,
  defineOutputLoader,
  loadOutputs,
  NotebookExportError,
  openExport,
  scalarLoader,
} from "../src/index.js";
import type { ExportOutput, ScalarValue } from "../src/index.js";
import { exportFixture, type MutableJsonObject } from "./fixture.js";

test("loads a heterogeneous output set with each loader's result type", async () => {
  const fixture = await exportFixture();
  const notebookExport = await openExport("https://example.test/stocks", { fetch: fixture.fetch });
  const outputs = await loadOutputs(notebookExport.state("alpha"), {
    count: scalarLoader(),
    view: defineBlobAssetLoader({
      mediaTypes: "application/vnd.example.fixture+json",
      load: ({ payload }) => new TextDecoder().decode(payload.data),
    }),
  });

  expectTypeOf(outputs).toEqualTypeOf<{ readonly count: ScalarValue; readonly view: string }>();
  expect(outputs).toEqual({ count: 1, view: '{"ready":true}' });
  expect(Object.isFrozen(outputs)).toBe(true);
});

test.each([false, true])(
  "settles canceled sibling loads with cleanup failure=%s",
  async (failsCleanup) => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });
    const base = notebookExport.defaultState;
    const primary = new Error("Output could not be decoded");
    const cleanup = new Error("Sibling cleanup failed");
    let settled = false;
    const state = {
      ...base,
      output(name: string): ExportOutput {
        return {
          ...base.output("count"),
          name,
          async load(_loader, options) {
            if (name === "broken") throw primary;
            return new Promise<never>((_resolve, reject) => {
              options?.signal?.addEventListener(
                "abort",
                () => {
                  queueMicrotask(() => {
                    settled = true;
                    reject(
                      failsCleanup ? cleanup : new NotebookExportError("abort", "Load aborted"),
                    );
                  });
                },
                { once: true },
              );
            });
          },
        };
      },
    };

    const loading = loadOutputs(state, { broken: scalarLoader(), sibling: scalarLoader() });
    if (failsCleanup) {
      await expect(loading).rejects.toMatchObject({ errors: [primary, cleanup] });
    } else {
      await expect(loading).rejects.toBe(primary);
    }
    expect(settled).toBe(true);
  },
);

test("rejects a retired load set before invoking its loaders", async () => {
  const fixture = await exportFixture();
  const notebookExport = await openExport("https://example.test/stocks", { fetch: fixture.fetch });
  const load = vi.fn(() => 7);
  const loader = defineOutputLoader({ codec: "marimo.scalar.v1", accepts: () => true, load });
  const controller = new AbortController();
  controller.abort("view retired");

  await expect(
    loadOutputs(
      notebookExport.defaultState,
      { count: loader },
      {
        signal: controller.signal,
      },
    ),
  ).rejects.toMatchObject({ code: "abort", cause: "view retired" });
  expect(load).not.toHaveBeenCalled();
});

test("forwards the caller's asset bound through each public output load", async () => {
  const fixture = await exportFixture();
  const notebookExport = await openExport("https://example.test/stocks", { fetch: fixture.fetch });
  const array = defineOutputLoader({
    codec: "numpy.npy.v1",
    accepts: () => true,
    load: ({ payload }) => payload,
  });

  await expect(
    loadOutputs(notebookExport.defaultState, { array }, { maxBytes: 7 }),
  ).rejects.toMatchObject({
    code: "read_limit_exceeded",
  });
});

test("validates output names before starting a load set", async () => {
  const fixture = await exportFixture();
  const notebookExport = await openExport("https://example.test/stocks", { fetch: fixture.fetch });
  const load = vi.fn(() => 7);
  const loader = defineOutputLoader({ codec: "marimo.scalar.v1", accepts: () => true, load });

  await expect(
    loadOutputs(notebookExport.defaultState, { count: loader, missing: loader }),
  ).rejects.toMatchObject({
    code: "output_not_found",
  });
  expect(load).not.toHaveBeenCalled();
});

test("rejects a callable then output before promise assimilation", async () => {
  const fixture = await exportFixture({
    indexTransform(index) {
      index.outputs = ["then"];
      // SAFETY: exportFixture constructs these mutable state and output records.
      for (const state of Object.values(index.states as MutableJsonObject)) {
        // SAFETY: exportFixture supplies an object for every state entry.
        const record = state as MutableJsonObject;
        // SAFETY: exportFixture supplies an output record on each state.
        const outputs = record.outputs as MutableJsonObject;
        // The legal output name intentionally exercises Promise's reserved field.
        // oxlint-disable-next-line unicorn/no-thenable
        record.outputs = { then: outputs.count! };
      }
    },
  });
  const notebookExport = await openExport("https://example.test/stocks", { fetch: fixture.fetch });
  const callable = vi.fn((resolve: (value: string) => void) => resolve("assimilated"));
  const loader = defineOutputLoader({
    codec: "marimo.scalar.v1",
    accepts: () => true,
    load: () => callable,
  });

  // The decoded function must be rejected before Promise resolution can invoke it.
  // oxlint-disable-next-line unicorn/no-thenable
  const loading = loadOutputs(notebookExport.defaultState, { then: loader });
  await expect(loading).rejects.toMatchObject({
    code: "decode_failed",
    details: { output: "then" },
  });
  expect(callable).not.toHaveBeenCalled();
});
