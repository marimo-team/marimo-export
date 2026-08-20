import { describe, expect, test } from "vite-plus/test";

import type {
  PreparedWidgetGraphPort,
  PreparedWidgetGraphSnapshot,
} from "../src/runtime/prepared-graph.js";

import {
  PreparedWidgetGraph,
  PreparedWidgetGraphReplacementError,
} from "../src/runtime/prepared-graph.js";

interface GraphRecord {
  readonly id: string;
  readonly active: boolean;
  readonly revision: string;
  readonly module: string | undefined;
  readonly state: Readonly<Record<string, unknown>>;
  readonly failValidation?: boolean;
  readonly failPreflight?: boolean;
  readonly failReplay?: boolean;
}

interface LiveModel {
  module: string | undefined;
  state: Record<string, unknown>;
}

const record = (
  id: string,
  revision: string,
  state: Readonly<Record<string, unknown>>,
  options: Partial<Omit<GraphRecord, "id" | "revision" | "state">> = {},
): GraphRecord => ({
  id,
  revision,
  state,
  active: true,
  module: "module-a",
  ...options,
});

const graph = (
  records: readonly GraphRecord[],
  files: Readonly<Record<string, string>> = {},
): PreparedWidgetGraphSnapshot<GraphRecord> => ({
  files,
  records: new Map(records.map((value) => [value.id, value])),
});

const arbitraryAbort = (): AbortSignal => {
  const controller = new AbortController();
  controller.abort({ source: "caller" });
  return controller.signal;
};

class TestGraphPort implements PreparedWidgetGraphPort<GraphRecord, LiveModel> {
  readonly models = new Map<string, LiveModel>();
  readonly events: string[] = [];
  readonly closeFailures = new Set<string>();
  readonly persistentCloseFailures = new Set<string>();
  readonly captureFailures = new Set<string>();
  readonly replayFailures = new Set<string>();
  readonly restoreFailures = new Set<string>();
  files: Readonly<Record<string, string>> = {};

  id(value: GraphRecord): string {
    return value.id;
  }

  active(value: GraphRecord): boolean {
    return value.active;
  }

  same(left: GraphRecord, right: GraphRecord): boolean {
    return left.revision === right.revision;
  }

  changesModule(previous: GraphRecord, next: GraphRecord): boolean {
    return previous.module !== next.module;
  }

  capture(id: string): LiveModel {
    if (this.captureFailures.has(id)) throw new Error(`Capture failed for ${id}`);
    const model = this.models.get(id);
    if (model === undefined) throw new Error(`Missing live model ${id}`);
    return structuredClone(model);
  }

  merge(value: GraphRecord, live: LiveModel): GraphRecord {
    const state = structuredClone(live.state);
    return {
      ...value,
      state,
      revision: `live:${JSON.stringify(state)}`,
    };
  }

  async replay(value: GraphRecord, signal?: AbortSignal): Promise<void> {
    signal?.throwIfAborted();
    this.events.push(`replay:${value.id}:${value.revision}`);
    if (this.replayFailures.delete(value.id) || value.failReplay) {
      throw new Error(`Replay failed for ${value.id}`);
    }
    this.models.set(value.id, {
      module: value.module,
      state: structuredClone(value.state),
    });
  }

  restore(id: string, state: LiveModel): void {
    this.events.push(`restore:${id}`);
    if (this.restoreFailures.delete(id)) throw new Error(`Restore failed for ${id}`);
    const model = this.models.get(id);
    if (model === undefined) throw new Error(`Missing model ${id} during restore`);
    model.module = state.module;
    model.state = structuredClone(state.state);
  }

  async close(id: string): Promise<void> {
    this.events.push(`close:${id}`);
    if (this.persistentCloseFailures.has(id) || this.closeFailures.delete(id)) {
      throw new Error(`Close failed for ${id}`);
    }
    this.models.delete(id);
  }

  setFiles(files: Readonly<Record<string, string>>): void {
    this.files = Object.freeze({ ...files });
    this.events.push(`files:${Object.keys(files).sort().join(",")}`);
  }

  async validate(value: GraphRecord, signal?: AbortSignal): Promise<void> {
    signal?.throwIfAborted();
    if (value.failValidation) throw new Error(`Validation failed for ${value.id}`);
  }

  async preflight(value: GraphRecord, signal?: AbortSignal): Promise<void> {
    signal?.throwIfAborted();
    if (value.failPreflight) throw new Error(`Preflight failed for ${value.id}`);
  }
}

const commit = async (
  runtime: PreparedWidgetGraph<GraphRecord, LiveModel>,
  next: PreparedWidgetGraphSnapshot<GraphRecord>,
) => {
  const replacement = await runtime.replace(next);
  await replacement.commit();
  return replacement;
};

describe("prepared AnyWidget graph", () => {
  test("returns the adopted graph when a replacement commits", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    const target = graph([record("model-0", "first", { count: 1 })], { first: "one" });
    const replacement = await runtime.replace(target);

    const adopted = await replacement.commit();

    expect(adopted?.files).toEqual({ first: "one" });
    expect(adopted?.records.get("model-0")?.revision).toBe("first");
    await runtime.dispose();
  });

  test("normalizes pre-aborted no-op and mutating replacements", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);

    await expect(runtime.replace(graph([]), arbitraryAbort())).rejects.toMatchObject({
      name: "AbortError",
    });
    await commit(runtime, graph([record("model-0", "first", { count: 1 })]));
    await expect(
      runtime.replace(graph([record("model-0", "second", { count: 2 })]), arbitraryAbort()),
    ).rejects.toMatchObject({ name: "AbortError" });

    expect(port.models.get("model-0")?.state).toEqual({ count: 1 });
    await runtime.dispose();
  });

  test("checkpoints live browser state and restores it after another generation", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    await commit(runtime, graph([record("model-0", "first", { count: 1 })], { first: "one" }));
    port.models.get("model-0")!.state.count = 7;
    const checkpoint = runtime.checkpoint();
    expect(Object.isFrozen(checkpoint)).toBe(true);
    expect(Object.keys(checkpoint)).toEqual([]);
    expect("snapshot" in checkpoint).toBe(false);

    await commit(runtime, graph([record("model-0", "second", { count: 2 })], { second: "two" }));
    expect(port.models.get("model-0")?.state).toEqual({ count: 2 });

    const replacement = await runtime.replace(checkpoint);
    await replacement.commit();

    expect(replacement.mutated).toBe(true);
    expect(replacement.remount).toBe(false);
    expect(port.models.get("model-0")?.state).toEqual({ count: 7 });
    expect(port.files).toEqual({ first: "one" });
    await runtime.dispose();
  });

  test("rolls back a stable update to the captured live state", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    await commit(runtime, graph([record("model-0", "first", { count: 1 })]));
    port.models.get("model-0")!.state.count = 9;

    const replacement = await runtime.replace(graph([record("model-0", "second", { count: 2 })]));
    expect(port.models.get("model-0")?.state).toEqual({ count: 2 });

    await replacement.rollback();

    expect(port.models.get("model-0")?.state).toEqual({ count: 9 });
    expect(port.events).toContain("restore:model-0");
    await runtime.dispose();
  });

  test("keeps rollback final when commit is requested afterward", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    await commit(runtime, graph([record("model-0", "first", { count: 1 })]));
    port.models.get("model-0")!.state.count = 9;
    const replacement = await runtime.replace(graph([record("model-0", "second", { count: 2 })]));

    await replacement.rollback();
    await replacement.commit();

    expect(port.models.get("model-0")?.state).toEqual({ count: 9 });
    await runtime.dispose();
  });

  test("replays the previous module and live state when replacement rolls back", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    await commit(runtime, graph([record("model-0", "first", { count: 1 })]));
    port.models.get("model-0")!.state.count = 11;

    const replacement = await runtime.replace(
      graph([record("model-0", "second", { count: 2 }, { module: "module-b" })]),
    );
    expect(replacement.remount).toBe(true);
    expect(port.models.get("model-0")).toEqual({ module: "module-b", state: { count: 11 } });

    await replacement.rollback();

    expect(port.models.get("model-0")).toEqual({ module: "module-a", state: { count: 11 } });
    await runtime.dispose();
  });

  test("restores models removed before a commit failure", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    await commit(
      runtime,
      graph([record("model-0", "first", { count: 1 }), record("model-1", "first", { count: 2 })]),
    );
    port.models.get("model-0")!.state.count = 5;
    port.models.get("model-1")!.state.count = 6;
    port.closeFailures.add("model-1");
    const replacement = await runtime.replace(graph([]));

    await expect(replacement.commit()).rejects.toThrow("Close failed for model-1");
    await replacement.rollback();

    expect(port.models.get("model-0")?.state).toEqual({ count: 5 });
    expect(port.models.get("model-1")?.state).toEqual({ count: 6 });
    await runtime.dispose();
  });

  test("preserves partial removal commit and rollback failures", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    await commit(
      runtime,
      graph([record("model-0", "first", { count: 1 }), record("model-1", "first", { count: 2 })]),
    );
    port.closeFailures.add("model-1");
    port.replayFailures.add("model-0");
    const replacement = await runtime.replace(graph([]));

    const commitFailure = await replacement.commit().catch((error: unknown) => error);
    const rollbackFailure = await replacement.rollback().catch((error: unknown) => error);

    expect(commitFailure).toMatchObject({ message: "Close failed for model-1" });
    expect(rollbackFailure).toBeInstanceOf(PreparedWidgetGraphReplacementError);
    expect(rollbackFailure).toMatchObject({ remount: true, cause: expect.any(AggregateError) });
    expect((rollbackFailure as PreparedWidgetGraphReplacementError).cause).toMatchObject({
      errors: [commitFailure, expect.objectContaining({ message: "Replay failed for model-0" })],
    });
    await expect(runtime.dispose()).rejects.toBe(rollbackFailure);
  });

  test("marks an added-model close failure as requiring remount", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    const replacement = await runtime.replace(graph([record("model-0", "first", { count: 1 })]));
    port.closeFailures.add("model-0");

    const failure = await replacement.rollback().catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(PreparedWidgetGraphReplacementError);
    expect(failure).toMatchObject({
      remount: true,
      cause: expect.objectContaining({ message: "Close failed for model-0" }),
    });
    await expect(runtime.dispose()).rejects.toBe(failure);
  });

  test("marks a stable-state restore failure as requiring remount", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    await commit(runtime, graph([record("model-0", "first", { count: 1 })]));
    const replacement = await runtime.replace(graph([record("model-0", "second", { count: 2 })]));
    port.restoreFailures.add("model-0");

    const failure = await replacement.rollback().catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(PreparedWidgetGraphReplacementError);
    expect(failure).toMatchObject({
      remount: true,
      cause: expect.objectContaining({ message: "Restore failed for model-0" }),
    });
    await expect(runtime.dispose()).rejects.toBe(failure);
  });

  test("restores the prior graph when staged replay fails", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    await commit(runtime, graph([record("model-0", "first", { count: 1 })]));
    port.models.get("model-0")!.state.count = 4;

    await expect(
      runtime.replace(graph([record("model-0", "second", { count: 2 }, { failReplay: true })])),
    ).rejects.toThrow("Replay failed for model-0");

    expect(port.models.get("model-0")?.state).toEqual({ count: 4 });
    await runtime.dispose();
  });

  test("marks failures after model identity changes as requiring remount", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    await commit(runtime, graph([record("model-0", "first", { count: 1 })]));

    await expect(
      runtime.replace(
        graph([
          record(
            "model-0",
            "second",
            { count: 2 },
            {
              module: "module-b",
              failReplay: true,
            },
          ),
        ]),
      ),
    ).rejects.toBeInstanceOf(PreparedWidgetGraphReplacementError);
    expect(port.models.get("model-0")?.module).toBe("module-a");
    await runtime.dispose();
  });

  test("wraps replacement and rollback failures as requiring remount", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    await commit(runtime, graph([record("model-0", "first", { count: 1 })]));
    port.persistentCloseFailures.add("model-0");

    const failure = await runtime
      .replace(graph([record("model-0", "second", { count: 2 }, { module: "module-b" })]))
      .catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(PreparedWidgetGraphReplacementError);
    expect(failure).toMatchObject({ remount: true, cause: expect.any(AggregateError) });
    expect((failure as PreparedWidgetGraphReplacementError).cause).toMatchObject({
      errors: [expect.any(Error), expect.any(Error)],
    });
    port.persistentCloseFailures.clear();
    await runtime.dispose();
  });

  test("restores files when validation rejects before mutation", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    await commit(runtime, graph([record("model-0", "first", {})], { stable: "one" }));

    await expect(
      runtime.replace(
        graph([record("model-1", "next", {}, { failValidation: true })], { staged: "two" }),
      ),
    ).rejects.toThrow("Validation failed for model-1");

    expect(port.files).toEqual({ stable: "one" });
    expect(port.models.has("model-0")).toBe(true);
    await runtime.dispose();
  });

  test("restores files when live-state capture rejects", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    await commit(runtime, graph([record("model-0", "first", {})], { stable: "one" }));
    port.captureFailures.add("model-0");

    await expect(
      runtime.replace(graph([record("model-0", "next", { count: 2 })], { staged: "two" })),
    ).rejects.toThrow("Capture failed for model-0");

    expect(port.files).toEqual({ stable: "one" });
    port.captureFailures.clear();
    await runtime.dispose();
  });

  test("disposal rolls back an unsettled replacement and closes the committed graph", async () => {
    const port = new TestGraphPort();
    const runtime = new PreparedWidgetGraph(port);
    await commit(runtime, graph([record("model-0", "first", { count: 1 })]));
    const replacement = await runtime.replace(graph([record("model-0", "second", { count: 2 })]));

    await runtime.dispose();
    await replacement.rollback();

    expect(port.models.size).toBe(0);
    expect(port.files).toEqual({});
  });
});
