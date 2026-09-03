import { describe, expect, it, vi } from "vite-plus/test";

import type {
  PreparedExportManifest,
  PreparedPublication,
  PreparedPublicationRefreshDependencies,
  PreparedStateChange,
} from "../src/prepared/index.js";
import { PreparedPublicationRefresh, PreparedStateController } from "../src/prepared/index.js";
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
      return Object.freeze({ ...publication, manifest });
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

describe("prepared publication refresh", () => {
  it("opens the initial manifest and commits its selected state", async () => {
    const notebookExport = preparedExportFixture({ inputs: [{ mode: "baseline" }] });
    const publication = preparedPublicationFixture(notebookExport, { mode: "baseline" });
    const harness = refreshHarness([publication]);

    await harness.refresh.start();

    expect(harness.fetchManifest).toHaveBeenCalledOnce();
    expect(harness.openPublication).toHaveBeenCalledOnce();
    expect(harness.applied).toHaveLength(1);
    expect(harness.state.snapshot().current?.state.inputs).toEqual({ mode: "baseline" });
  });

  it("reuses an unchanged immutable export while applying updated manifest state", async () => {
    const notebookExport = preparedExportFixture({
      inputs: [{ mode: "baseline" }, { mode: "alternate" }],
    });
    const baseline = preparedPublicationFixture(notebookExport, { mode: "baseline" });
    const alternate = preparedPublicationFixture(notebookExport, { mode: "alternate" });
    const harness = refreshHarness([baseline, alternate]);

    await harness.refresh.start();
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

  it("preserves an available local selection across immutable export identities", async () => {
    const firstExport = preparedExportFixture({
      inputs: [{ mode: "baseline" }, { mode: "alternate" }],
    });
    const secondExport = preparedExportFixture({
      base: "https://example.test/export-2/",
      identity: "2".repeat(64),
      inputs: [{ mode: "baseline" }, { mode: "alternate" }],
    });
    const first = preparedPublicationFixture(firstExport, { mode: "baseline" });
    const second = preparedPublicationFixture(secondExport, { mode: "baseline" });
    const harness = refreshHarness([first, second]);
    await harness.refresh.start();
    await harness.state.updateInputs({ mode: "alternate" });

    await harness.refresh.refresh();

    expect(harness.state.snapshot().current?.notebookExport).toBe(secondExport);
    expect(harness.state.snapshot().current?.state.inputs).toEqual({ mode: "alternate" });
  });

  it("opens a matching immutable identity again when its base URL changes", async () => {
    const identity = "1".repeat(64);
    const firstExport = preparedExportFixture({
      base: "https://example.test/export-1/",
      identity,
      inputs: [{ mode: "baseline" }, { mode: "alternate" }],
    });
    const secondExport = preparedExportFixture({
      base: "https://example.test/export-2/",
      identity,
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
    const state = new PreparedStateController({ async apply() {} });
    const refresh = new PreparedPublicationRefresh(new URL("https://example.test/current"), state, {
      dependencies: { fetchManifest, openPublication },
    });

    await refresh.start();
    await state.updateInputs({ mode: "alternate" });
    await refresh.refresh();

    expect(openPublication).toHaveBeenCalledTimes(2);
    expect(state.snapshot().current?.notebookExport).toBe(secondExport);
    expect(state.snapshot().current?.state.inputs).toEqual({ mode: "alternate" });
  });

  it("opens a new immutable export and preserves a pending requested state", async () => {
    const firstExport = preparedExportFixture({ inputs: [{ count: 0 }] });
    const secondExport = preparedExportFixture({
      base: "https://example.test/export-2/",
      identity: "2".repeat(64),
      inputs: [{ count: 0 }, { count: 1 }],
    });
    const first = preparedPublicationFixture(firstExport, { count: 0 });
    const second = preparedPublicationFixture(secondExport, { count: 0 });
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
    let observedSignal: AbortSignal | undefined;
    const fetchManifest = vi.fn<PreparedPublicationRefreshDependencies["fetchManifest"]>(
      async (_url, options) => {
        observedSignal = options?.signal;
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
    await vi.waitFor(() => expect(observedSignal).toBeDefined());
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
});
