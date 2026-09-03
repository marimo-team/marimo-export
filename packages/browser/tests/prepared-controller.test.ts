import { describe, expect, it, vi } from "vite-plus/test";
import type { JsonValue } from "../src/types.js";

import type { PreparedStateChange, PreparedStatePort } from "../src/prepared/index.js";
import { PreparedStateController } from "../src/prepared/index.js";
import { preparedExportFixture, preparedPublicationFixture } from "./prepared-fixture.js";

const recordingPort = () => {
  const applied: PreparedStateChange[] = [];
  const port: PreparedStatePort = {
    async apply(change) {
      applied.push(change);
    },
  };
  return { applied, port };
};

describe("prepared state controller", () => {
  it("starts once and resolves sparse updates through the immutable export", async () => {
    const notebookExport = preparedExportFixture({
      inputs: [
        { count: 1, mode: "baseline" },
        { count: 2, mode: "baseline" },
      ],
    });
    const publication = preparedPublicationFixture(notebookExport, {
      count: 1,
      mode: "baseline",
    });
    const { applied, port } = recordingPort();
    const controller = new PreparedStateController(port);

    await controller.start(publication);
    await controller.updateInputs({ count: 2 });

    expect(applied.map((change) => change.reason)).toEqual(["start", "state"]);
    expect(controller.snapshot().current?.state.inputs).toEqual({
      count: 2,
      mode: "baseline",
    });
    expect(controller.snapshot().pendingInputs).toBeUndefined();
    expect(() => controller.start(publication)).toThrow(/already started/);
  });

  it("routes controls and query parameters without application-specific state logic", async () => {
    const notebookExport = preparedExportFixture({
      controlBindings: {
        count: { input: "filters", path: [{ kind: "key", value: "count" }] },
        formChild: { input: "filters", path: [{ kind: "element" }] },
      },
      inputs: [
        { filters: { count: 1 }, mode: "baseline" },
        { filters: { count: 2 }, mode: "baseline" },
        { filters: { count: 2 }, mode: "alternate" },
      ],
    });
    const { port } = recordingPort();
    const controller = new PreparedStateController(port);
    await controller.start(
      preparedPublicationFixture(notebookExport, {
        filters: { count: 1 },
        mode: "baseline",
      }),
    );

    await expect(controller.updateControl("missing", 2)).resolves.toBe(false);
    await expect(controller.updateControl("formChild", 2)).resolves.toBe(false);
    await expect(controller.updateControl("count", 2)).resolves.toBe(true);
    await expect(controller.updateQuery("?mode=alternate")).resolves.toBe(true);

    expect(controller.snapshot().current?.state.inputs).toEqual({
      filters: { count: 2 },
      mode: "alternate",
    });
  });

  it("distinguishes inherited control names from own reserved-name bindings", async () => {
    const bindings: Readonly<
      Record<string, { readonly input: string; readonly path: readonly [] }>
    > = JSON.parse('{"__proto__":{"input":"count","path":[]}}');
    const notebookExport = preparedExportFixture({
      controlBindings: bindings,
      inputs: [{ count: 0 }, { count: 1 }],
    });
    const { port } = recordingPort();
    const controller = new PreparedStateController(port);
    await controller.start(preparedPublicationFixture(notebookExport, { count: 0 }));

    await expect(controller.updateControl("toString", 1)).resolves.toBe(false);
    await expect(controller.updateControl("constructor", 1)).resolves.toBe(false);
    await expect(controller.updateControl("__proto__", 1)).resolves.toBe(true);
    expect(controller.snapshot().current?.state.inputs).toEqual({ count: 1 });
  });

  it("serializes rapid superseding transitions and commits the final request", async () => {
    const notebookExport = preparedExportFixture({
      inputs: Array.from({ length: 41 }, (_, count) => ({ count })),
    });
    let active = 0;
    let maximumActive = 0;
    const committed: number[] = [];
    const port: PreparedStatePort = {
      async apply(change, signal) {
        active += 1;
        maximumActive = Math.max(maximumActive, active);
        try {
          await new Promise<void>((resolve) => setTimeout(resolve, 1));
          signal.throwIfAborted();
          committed.push(numberValue(change.next.state.inputs.count));
        } finally {
          active -= 1;
        }
      },
    };
    const controller = new PreparedStateController(port);
    await controller.start(preparedPublicationFixture(notebookExport, { count: 0 }));

    const updates = Array.from({ length: 40 }, (_, index) =>
      controller.updateInputs({ count: index + 1 }),
    );
    const results = await Promise.allSettled(updates);

    expect(results.filter((result) => result.status === "fulfilled")).toHaveLength(1);
    expect(maximumActive).toBe(1);
    expect(committed.at(-1)).toBe(40);
    expect(controller.snapshot().current?.state.inputs).toEqual({ count: 40 });
  });

  it("restores the last committed state when application fails", async () => {
    const notebookExport = preparedExportFixture({ inputs: [{ count: 0 }, { count: 1 }] });
    const restore = vi.fn<NonNullable<PreparedStatePort["restore"]>>(async () => {});
    const port: PreparedStatePort = {
      async apply(change) {
        if (change.next.state.inputs.count === 1) {
          throw new Error("Renderer rejected state");
        }
      },
      restore,
    };
    const controller = new PreparedStateController(port);
    const baseline = preparedPublicationFixture(notebookExport, { count: 0 });
    await controller.start(baseline);

    await expect(controller.updateInputs({ count: 1 })).rejects.toThrow("Renderer rejected state");

    expect(restore).toHaveBeenCalledWith(baseline);
    expect(controller.snapshot().current).toBe(baseline);
    expect(controller.snapshot().pendingInputs).toBeUndefined();
  });

  it("keeps an unavailable request pending for a refreshed publication", async () => {
    const firstExport = preparedExportFixture({ inputs: [{ count: 0 }] });
    const secondExport = preparedExportFixture({
      base: "https://example.test/export-2/",
      identity: "2".repeat(64),
      inputs: [{ count: 0 }, { count: 1 }],
    });
    const { port } = recordingPort();
    const controller = new PreparedStateController(port);
    await controller.start(preparedPublicationFixture(firstExport, { count: 0 }));

    await expect(controller.updateInputs({ count: 1 })).rejects.toMatchObject({
      code: "state_unavailable",
    });
    expect(controller.snapshot().pendingInputs).toEqual({ count: 1 });

    await controller.replacePublication(preparedPublicationFixture(secondExport, { count: 0 }));

    expect(controller.snapshot().current?.notebookExport).toBe(secondExport);
    expect(controller.snapshot().current?.state.inputs).toEqual({ count: 1 });
    expect(controller.snapshot().pendingInputs).toBeUndefined();
  });

  it("drops pending inputs when a publication changes its input contract", async () => {
    const firstExport = preparedExportFixture({ inputs: [{ mode: "baseline" }] });
    const secondExport = preparedExportFixture({
      base: "https://example.test/export-2/",
      identity: "2".repeat(64),
      inputs: [{ choice: "A" }],
    });
    const { port } = recordingPort();
    const controller = new PreparedStateController(port);
    await controller.start(preparedPublicationFixture(firstExport, { mode: "baseline" }));
    await expect(controller.updateInputs({ mode: "alternate" })).rejects.toMatchObject({
      code: "state_unavailable",
    });

    await expect(
      controller.replacePublication(preparedPublicationFixture(secondExport, { choice: "A" })),
    ).resolves.toBeUndefined();

    expect(controller.snapshot().current?.state.inputs).toEqual({ choice: "A" });
    expect(controller.snapshot().pendingInputs).toBeUndefined();
  });

  it("clears a caller-aborted request before a later publication refresh", async () => {
    const firstExport = preparedExportFixture({
      inputs: [{ mode: "baseline" }, { mode: "alternate" }],
    });
    const secondExport = preparedExportFixture({
      base: "https://example.test/export-2/",
      identity: "2".repeat(64),
      inputs: [{ mode: "baseline" }, { mode: "alternate" }],
    });
    let entered: (() => void) | undefined;
    const started = new Promise<void>((resolve) => {
      entered = resolve;
    });
    const controller = new PreparedStateController({
      async apply(change, signal) {
        if (change.reason !== "state") return;
        entered?.();
        await new Promise<void>((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(signal.reason), { once: true });
        });
      },
    });
    await controller.start(preparedPublicationFixture(firstExport, { mode: "baseline" }));
    const request = new AbortController();
    const updating = controller.updateInputs({ mode: "alternate" }, request.signal);
    await started;

    request.abort(new DOMException("Caller cancelled", "AbortError"));
    await expect(updating).rejects.toMatchObject({ name: "AbortError" });
    expect(controller.snapshot().pendingInputs).toBeUndefined();

    await controller.replacePublication(
      preparedPublicationFixture(secondExport, { mode: "baseline" }),
    );
    expect(controller.snapshot().current?.state.inputs).toEqual({ mode: "baseline" });
  });

  it("updates manifest metadata without reapplying an unchanged immutable state", async () => {
    const notebookExport = preparedExportFixture({ inputs: [{ count: 0 }] });
    const { applied, port } = recordingPort();
    const controller = new PreparedStateController(port);
    await controller.start(preparedPublicationFixture(notebookExport, { count: 0 }));

    await controller.replacePublication(
      preparedPublicationFixture(notebookExport, { count: 0 }, { refreshIntervalMs: 1_000 }),
    );

    expect(applied).toHaveLength(1);
    expect(controller.snapshot().current?.manifest.refreshIntervalMs).toBe(1_000);
  });

  it("disposes transition and application ownership idempotently", async () => {
    const notebookExport = preparedExportFixture({ inputs: [{ count: 0 }] });
    const dispose = vi.fn(async () => {});
    const controller = new PreparedStateController({ async apply() {}, dispose });
    await controller.start(preparedPublicationFixture(notebookExport, { count: 0 }));

    await Promise.all([controller.dispose(), controller.dispose()]);

    expect(dispose).toHaveBeenCalledOnce();
    expect(controller.snapshot()).toMatchObject({ current: undefined, disposed: true });
    await expect(controller.updateInputs({ count: 0 })).rejects.toThrow(/disposed/);
  });

  it("releases application ownership when its lifecycle signal aborts", async () => {
    const notebookExport = preparedExportFixture({ inputs: [{ count: 0 }] });
    const lifecycle = new AbortController();
    const dispose = vi.fn(async () => {});
    const controller = new PreparedStateController({ async apply() {}, dispose }, lifecycle.signal);
    await controller.start(preparedPublicationFixture(notebookExport, { count: 0 }));

    lifecycle.abort(new DOMException("Application unmounted", "AbortError"));
    await vi.waitFor(() => expect(dispose).toHaveBeenCalledOnce());

    expect(controller.snapshot().disposed).toBe(true);
    expect(controller.snapshot().current).toBeUndefined();
  });
});

function numberValue(value: JsonValue | undefined): number {
  if (!isJsonNumber(value)) {
    throw new TypeError("Expected a numeric prepared-state fixture value.");
  }
  return value;
}

function isJsonNumber(value: JsonValue | undefined): value is number {
  return Object.prototype.toString.call(value) === "[object Number]";
}
