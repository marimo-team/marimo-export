import { describe, expect, test, vi } from "vite-plus/test";

import {
  fetchPreparedExportManifest,
  openPreparedPublication,
  PreparedPublicationRefresh,
  PreparedStateController,
} from "../src/prepared/index.js";
import { preparedExportFixture, preparedPublicationFixture } from "./prepared-fixture.js";

const arbitraryAbort = (): AbortSignal => {
  const controller = new AbortController();
  controller.abort({ source: "caller" });
  return controller.signal;
};

describe("prepared abort normalization", () => {
  test("normalizes every controller operation precheck", async () => {
    const notebookExport = preparedExportFixture({
      controlBindings: { count: { input: "count", path: [] } },
      inputs: [{ count: 0 }, { count: 1 }],
    });
    const publication = preparedPublicationFixture(notebookExport, { count: 0 });
    const unstarted = new PreparedStateController({ async apply() {} });

    expect(() => unstarted.start(publication, arbitraryAbort())).toThrow(
      expect.objectContaining({ name: "AbortError" }),
    );

    const controller = new PreparedStateController({ async apply() {} });
    await controller.start(publication);
    await expect(controller.updateInputs({ count: 1 }, arbitraryAbort())).rejects.toMatchObject({
      name: "AbortError",
    });
    await expect(controller.updateControl("count", 1, arbitraryAbort())).rejects.toMatchObject({
      name: "AbortError",
    });
    await expect(controller.updateQuery("?count=1", arbitraryAbort())).rejects.toMatchObject({
      name: "AbortError",
    });
    await expect(
      controller.replacePublication(publication, arbitraryAbort()),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(controller.snapshot().current?.state.inputs).toEqual({ count: 0 });
  });

  test("blocks work immediately for a pre-aborted controller lifecycle", async () => {
    const notebookExport = preparedExportFixture({ inputs: [{ count: 0 }] });
    const publication = preparedPublicationFixture(notebookExport, { count: 0 });
    const lifecycle = new AbortController();
    lifecycle.abort({ source: "application" });
    const controller = new PreparedStateController({ async apply() {} }, lifecycle.signal);

    expect(() => controller.start(publication)).toThrow(
      expect.objectContaining({ name: "AbortError" }),
    );
    await vi.waitFor(() => expect(controller.snapshot().disposed).toBe(true));
  });

  test("normalizes manifest fetch and open prechecks", async () => {
    const notebookExport = preparedExportFixture({ inputs: [{ count: 0 }] });
    const publication = preparedPublicationFixture(notebookExport, { count: 0 });
    const fetch = vi.fn();
    const openExport = vi.fn();

    await expect(
      fetchPreparedExportManifest(new URL("https://example.test/current"), {
        fetch,
        signal: arbitraryAbort(),
      }),
    ).rejects.toMatchObject({ name: "AbortError" });
    await expect(
      openPreparedPublication(publication.manifest, new URL("https://example.test/current"), {
        openExport,
        signal: arbitraryAbort(),
      }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetch).not.toHaveBeenCalled();
    expect(openExport).not.toHaveBeenCalled();
  });

  test("normalizes refresh start and refresh prechecks", () => {
    const state = new PreparedStateController({ async apply() {} });
    const refresh = new PreparedPublicationRefresh(new URL("https://example.test/current"), state);

    expect(() => refresh.start(arbitraryAbort())).toThrow(
      expect.objectContaining({ name: "AbortError" }),
    );
    expect(() => refresh.refresh(arbitraryAbort())).toThrow(
      expect.objectContaining({ name: "AbortError" }),
    );
  });
});
