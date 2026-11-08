import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import type {
  PreparedExportManifest,
  PreparedPublication,
  PreparedPublicationRefreshDependencies,
  PreparedStateChange,
} from "../src/prepared/index.js";
import {
  PreparedPublicationRefresh,
  PreparedStateController,
  resolvePreparedPublication,
} from "../src/prepared/index.js";
import { defineOutputLoader } from "../src/index.js";
import { exportFixture, mutableObject, stringValue } from "./fixture.js";
import type { Fixture } from "./fixture.js";
import {
  preparedExportFixture,
  preparedManifestFixture,
  preparedPublicationFixture,
} from "./prepared-fixture.js";

const refreshHarness = (publications: readonly PreparedPublication[]) => {
  const manifests = publications.map((publication) => publication.manifest);
  let fetchIndex = 0;
  const fetchManifest = vi.fn(async () => manifests[Math.min(fetchIndex++, manifests.length - 1)]!);
  const openPublication = vi.fn(
    async (manifest: PreparedExportManifest): Promise<PreparedPublication> => {
      const publication = publications.find(
        (candidate) => candidate.manifest.instance === manifest.instance,
      );
      if (publication === undefined) {
        throw new Error("Fixture publication missing");
      }
      return resolvePreparedPublication(
        manifest,
        new URL("https://example.test/current"),
        publication.notebookExport,
      );
    },
  );
  const applied: PreparedStateChange[] = [];
  const state = new PreparedStateController({
    async apply(change) {
      applied.push(change);
    },
  });
  const refresh = new PreparedPublicationRefresh(new URL("https://example.test/current"), state, {
    dependencies: { fetchManifest, openPublication },
  });
  return { applied, fetchManifest, openPublication, refresh, state };
};

afterEach(() => {
  vi.unstubAllGlobals();
});

const manifestDocument = (fixture: Fixture, alias: string, instance: string, exportUrl: string) => {
  const fingerprint = stringValue(mutableObject(fixture.index.aliases, "aliases")[alias], alias);
  const state = mutableObject(mutableObject(fixture.index.states, "states")[fingerprint], alias);
  return JSON.stringify({
    schema: "marimo-export.prepared.v1",
    instance,
    export_url: exportUrl,
    inputs: state.inputs,
    state_fingerprint: fingerprint,
  });
};

describe("prepared publication refresh", () => {
  it("follows real exports by manifest instance on an origin without crypto.subtle", async () => {
    const first = await exportFixture({ basePath: "/first/" });
    const second = await exportFixture({ basePath: "/second/" });
    let manifest = manifestDocument(first, "alpha", "1".repeat(64), "./first/");
    const fetch: typeof globalThis.fetch = async (input, init) => {
      const url = input instanceof Request ? input.url : input.toString();
      if (new URL(url).pathname === "/current") return new Response(manifest);
      return (url.includes("/first/") ? first : second).fetch(input, init);
    };
    vi.stubGlobal("crypto", { subtle: undefined });
    const arrayLoader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load: ({ payload }) => payload,
    });
    const loads: string[] = [];
    const state = new PreparedStateController({
      async apply({ next }, signal) {
        await next.state.output("array").load(arrayLoader, { signal });
        loads.push(`${next.notebookExport.base.pathname}${String(next.state.aliases[0])}`);
      },
    });
    const refresh = new PreparedPublicationRefresh(new URL("http://insecure.test/current"), state, {
      fetch,
    });
    const indexReads = (fixture: Fixture) =>
      fixture.requests.filter((request) => request.endsWith("/index.json")).length;

    await refresh.start();
    manifest = manifestDocument(first, "zeta", "1".repeat(64), "./first/");
    await refresh.refresh();
    expect(indexReads(first)).toBe(1);

    manifest = manifestDocument(second, "alpha", "2".repeat(64), "./second/");
    await refresh.refresh();

    expect(indexReads(second)).toBe(1);

    manifest = manifestDocument(second, "alpha", "3".repeat(64), "./second/");
    await refresh.refresh();

    expect(indexReads(second)).toBe(2);
    expect(loads).toEqual(["/first/alpha", "/first/zeta", "/second/alpha", "/second/alpha"]);
    await refresh.dispose();
    await state.dispose();
  });

  it("rejects a manifest whose export URL differs from the opened export", () => {
    const notebookExport = preparedExportFixture({
      base: "https://example.test/opened/",
      inputs: [{ mode: "baseline" }],
    });
    const manifest = preparedManifestFixture(
      notebookExport,
      { mode: "baseline" },
      { exportUrl: "https://example.test/elsewhere/" },
    );

    expect(() =>
      resolvePreparedPublication(manifest, new URL("https://example.test/current"), notebookExport),
    ).toThrow(expect.objectContaining({ code: "manifest_invalid" }));
  });

  it("reuses an unchanged immutable export while applying updated manifest state", async () => {
    const notebookExport = preparedExportFixture({
      inputs: [{ mode: "baseline" }, { mode: "alternate" }],
    });
    const baseline = preparedPublicationFixture(notebookExport, { mode: "baseline" });
    const alternate = preparedPublicationFixture(notebookExport, { mode: "alternate" });
    const harness = refreshHarness([baseline, alternate]);

    await harness.refresh.start();
    expect(harness.state.snapshot().current?.state.inputs).toEqual({ mode: "baseline" });

    await harness.refresh.refresh();

    expect(harness.openPublication).toHaveBeenCalledOnce();
    expect(harness.applied).toHaveLength(2);
    expect(harness.state.snapshot().current?.state.inputs).toEqual({ mode: "alternate" });
  });

  it("preserves a local selection when polling returns the unchanged manifest token", async () => {
    const notebookExport = preparedExportFixture({
      inputs: [{ mode: "baseline" }, { mode: "alternate" }],
    });
    const baseline = preparedPublicationFixture(notebookExport, { mode: "baseline" });
    const harness = refreshHarness([baseline, baseline]);
    await harness.refresh.start();
    await harness.state.updateInputs({ mode: "alternate" });

    await harness.refresh.refresh();

    expect(harness.openPublication).toHaveBeenCalledOnce();
    expect(harness.applied).toHaveLength(2);
    expect(harness.state.snapshot().current?.state.inputs).toEqual({ mode: "alternate" });
  });

  it("preserves an available local selection across immutable export instances", async () => {
    const firstExport = preparedExportFixture({
      inputs: [{ mode: "baseline" }, { mode: "alternate" }],
    });
    const secondExport = preparedExportFixture({
      base: "https://example.test/export-2/",
      inputs: [{ mode: "baseline" }, { mode: "alternate" }],
    });
    const first = preparedPublicationFixture(firstExport, { mode: "baseline" });
    const second = preparedPublicationFixture(
      secondExport,
      { mode: "baseline" },
      { instance: "2".repeat(64) },
    );
    const harness = refreshHarness([first, second]);
    await harness.refresh.start();
    await harness.state.updateInputs({ mode: "alternate" });

    await harness.refresh.refresh();

    expect(harness.state.snapshot().current?.notebookExport).toBe(secondExport);
    expect(harness.state.snapshot().current?.state.inputs).toEqual({ mode: "alternate" });
  });

  it.each([true, false])(
    "resolves a selection completed during publication opening when available=%s",
    async (available) => {
      const firstExport = preparedExportFixture({
        inputs: [{ mode: "baseline" }, { mode: "alternate" }],
      });
      const secondExport = preparedExportFixture({
        base: "https://example.test/export-2/",
        inputs: available ? [{ mode: "baseline" }, { mode: "alternate" }] : [{ mode: "baseline" }],
      });
      const first = preparedPublicationFixture(firstExport, { mode: "baseline" });
      const second = preparedPublicationFixture(
        secondExport,
        { mode: "baseline" },
        { instance: "2".repeat(64) },
      );
      const harness = refreshHarness([first, second]);
      await harness.refresh.start();
      let finishOpening = () => {};
      const opening = new Promise<void>((resolve) => {
        finishOpening = resolve;
      });
      let markOpening = () => {};
      const openingStarted = new Promise<void>((resolve) => {
        markOpening = resolve;
      });
      harness.openPublication.mockImplementationOnce(async () => {
        markOpening();
        await opening;
        return second;
      });
      const refreshing = harness.refresh.refresh();
      await openingStarted;

      await harness.state.updateInputs({ mode: "alternate" });
      finishOpening();
      await refreshing;

      expect(harness.state.snapshot().current?.notebookExport).toBe(secondExport);
      expect(harness.state.snapshot().current?.state.inputs).toEqual({
        mode: available ? "alternate" : "baseline",
      });
      await harness.refresh.dispose();
      await harness.state.dispose();
    },
  );

  it("opens a matching instance again when its export URL changes", async () => {
    const firstExport = preparedExportFixture({
      base: "https://example.test/export-1/",
      inputs: [{ mode: "baseline" }, { mode: "alternate" }],
    });
    const secondExport = preparedExportFixture({
      base: "https://example.test/export-2/",
      inputs: [{ mode: "baseline" }, { mode: "alternate" }],
    });
    const first = preparedPublicationFixture(firstExport, { mode: "baseline" });
    const second = preparedPublicationFixture(secondExport, { mode: "baseline" });
    const manifests = [first.manifest, second.manifest];
    let manifestIndex = 0;
    const fetchManifest = vi.fn(async () => manifests[manifestIndex++]!);
    const openPublication = vi.fn(async (manifest: PreparedExportManifest) =>
      manifest.exportUrl === firstExport.base.href ? first : second,
    );
    const applied: PreparedStateChange[] = [];
    const state = new PreparedStateController({
      async apply(change) {
        applied.push(change);
      },
    });
    const refresh = new PreparedPublicationRefresh(new URL("https://example.test/current"), state, {
      dependencies: { fetchManifest, openPublication },
    });

    await refresh.start();
    await state.updateInputs({ mode: "alternate" });
    await refresh.refresh();

    expect(openPublication).toHaveBeenCalledTimes(2);
    expect(applied.at(-1)?.next.notebookExport).toBe(secondExport);
    expect(state.snapshot().current?.notebookExport).toBe(secondExport);
    expect(state.snapshot().current?.state.inputs).toEqual({ mode: "alternate" });
  });

  it("opens a new immutable export and preserves a pending requested state", async () => {
    const firstExport = preparedExportFixture({ inputs: [{ count: 0 }] });
    const secondExport = preparedExportFixture({
      base: "https://example.test/export-2/",
      inputs: [{ count: 0 }, { count: 1 }],
    });
    const first = preparedPublicationFixture(firstExport, { count: 0 });
    const second = preparedPublicationFixture(
      secondExport,
      { count: 0 },
      { instance: "2".repeat(64) },
    );
    const harness = refreshHarness([first, second]);
    await harness.refresh.start();
    await expect(harness.state.updateInputs({ count: 1 })).rejects.toMatchObject({
      code: "state_unavailable",
    });

    await harness.refresh.refresh();

    expect(harness.openPublication).toHaveBeenCalledTimes(2);
    expect(harness.state.snapshot().current?.notebookExport).toBe(secondExport);
    expect(harness.state.snapshot().current?.state.inputs).toEqual({ count: 1 });
  });

  it("retains the last good publication when refresh fails", async () => {
    const notebookExport = preparedExportFixture({ inputs: [{ mode: "baseline" }] });
    const publication = preparedPublicationFixture(notebookExport, { mode: "baseline" });
    const harness = refreshHarness([publication]);
    await harness.refresh.start();
    harness.fetchManifest.mockRejectedValueOnce(new Error("Manifest unavailable"));

    await expect(harness.refresh.refresh()).rejects.toThrow("Manifest unavailable");

    expect(harness.applied).toHaveLength(1);
    expect(harness.state.snapshot().current).toMatchObject({ state: publication.state });
  });

  it("polls at the manifest interval and reports background failures", async () => {
    vi.useFakeTimers();
    try {
      const notebookExport = preparedExportFixture({ inputs: [{ mode: "baseline" }] });
      const base = preparedPublicationFixture(notebookExport, { mode: "baseline" });
      const manifest = preparedManifestFixture(
        notebookExport,
        { mode: "baseline" },
        {
          refreshIntervalMs: 250,
        },
      );
      const publication = Object.freeze({ ...base, manifest });
      const onError = vi.fn();
      const fetchManifest = vi
        .fn<PreparedPublicationRefreshDependencies["fetchManifest"]>()
        .mockResolvedValueOnce(manifest)
        .mockRejectedValueOnce(new Error("Poll failed"));
      const state = new PreparedStateController({ async apply() {} });
      const refresh = new PreparedPublicationRefresh(
        new URL("https://example.test/current"),
        state,
        {
          dependencies: {
            fetchManifest,
            openPublication: async () => publication,
          },
          onError,
        },
      );
      await refresh.start();

      await vi.advanceTimersByTimeAsync(250);

      expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: "Poll failed" }));
      await refresh.dispose();
    } finally {
      vi.useRealTimers();
    }
  });

  it("aborts active refresh work and releases polling ownership", async () => {
    const notebookExport = preparedExportFixture({ inputs: [{ mode: "baseline" }] });
    const publication = preparedPublicationFixture(notebookExport, { mode: "baseline" });
    let started = () => {};
    const fetching = new Promise<void>((resolve) => {
      started = resolve;
    });
    let observedSignal: AbortSignal | undefined;
    const fetchManifest = vi.fn<PreparedPublicationRefreshDependencies["fetchManifest"]>(
      async (_url, options) => {
        observedSignal = options?.signal;
        started();
        await new Promise<void>((_resolve, reject) => {
          options?.signal?.addEventListener("abort", () => reject(options.signal?.reason), {
            once: true,
          });
        });
        return publication.manifest;
      },
    );
    const state = new PreparedStateController({ async apply() {} });
    const refresh = new PreparedPublicationRefresh(new URL("https://example.test/current"), state, {
      dependencies: {
        fetchManifest,
        openPublication: async () => publication,
      },
    });

    const starting = refresh.start();
    await fetching;
    await refresh.dispose();

    await expect(starting).rejects.toMatchObject({ name: "AbortError" });
    expect(observedSignal?.aborted).toBe(true);
  });

  it("stops refreshing when its application lifecycle aborts", async () => {
    const notebookExport = preparedExportFixture({ inputs: [{ mode: "baseline" }] });
    const publication = preparedPublicationFixture(notebookExport, { mode: "baseline" });
    const lifecycle = new AbortController();
    const fetchManifest = vi.fn(async () => publication.manifest);
    const state = new PreparedStateController({ async apply() {} });
    const refresh = new PreparedPublicationRefresh(new URL("https://example.test/current"), state, {
      dependencies: {
        fetchManifest,
        openPublication: async () => publication,
      },
      signal: lifecycle.signal,
    });
    await refresh.start();

    lifecycle.abort(new DOMException("Application unmounted", "AbortError"));
    await refresh.refresh();

    expect(fetchManifest).toHaveBeenCalledOnce();
  });

  it("retains the committed publication when opening finishes after disposal", async () => {
    const first = preparedPublicationFixture(
      preparedExportFixture({ inputs: [{ mode: "baseline" }] }),
      { mode: "baseline" },
    );
    const second = preparedPublicationFixture(
      preparedExportFixture({ inputs: [{ mode: "replacement" }] }),
      { mode: "replacement" },
      { instance: "2".repeat(64) },
    );
    const harness = refreshHarness([first, second]);
    await harness.refresh.start();
    let entered = () => {};
    const opening = new Promise<void>((resolve) => {
      entered = resolve;
    });
    let finish = () => {};
    const ready = new Promise<void>((resolve) => {
      finish = resolve;
    });
    harness.openPublication.mockImplementationOnce(async () => {
      entered();
      await ready;
      return second;
    });

    const refreshing = harness.refresh.refresh();
    const rejected = expect(refreshing).rejects.toMatchObject({ name: "AbortError" });
    await opening;
    const disposal = harness.refresh.dispose();
    finish();
    await Promise.all([rejected, disposal]);

    expect(harness.applied).toHaveLength(1);
    expect(harness.state.snapshot().current?.state.inputs).toEqual({ mode: "baseline" });
    await harness.state.dispose();
  });
});
