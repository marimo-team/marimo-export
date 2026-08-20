import { runInNewContext } from "node:vm";

import { describe, expect, test } from "vite-plus/test";

import {
  NotebookExportError,
  defineOutputLoader,
  isNotebookExportError,
  openExport,
} from "../src/index.js";
import type { NotebookExportErrorCode } from "../src/index.js";
import { exportFixture } from "./fixture.js";

const NOTEBOOK_EXPORT_ERROR_BRAND = Symbol.for("@marimo-team/marimo-export.NotebookExportError.v1");

describe("export errors", () => {
  test("recognizes the public branded error contract", () => {
    const codes: readonly NotebookExportErrorCode[] = [
      "abort",
      "asset_invalid",
      "decode_failed",
      "integrity_failed",
      "loader_ambiguous",
      "loader_invalid",
      "loader_unavailable",
      "output_not_found",
      "output_representation_changed",
      "export_invalid",
      "export_noncanonical",
      "read_failed",
      "read_limit_exceeded",
      "state_input_invalid",
      "state_not_found",
      "state_unavailable",
    ];

    for (const code of codes) {
      expect(isNotebookExportError(new NotebookExportError(code, ""))).toBe(true);
    }
    expect(
      () => new NotebookExportError("unknown" as NotebookExportErrorCode, "typed failure"),
    ).toThrow(/known code/u);
    expect(() => new NotebookExportError("abort", 42 as never)).toThrow(
      /message must be a string/u,
    );
  });

  test("wraps synchronous and asynchronous loader failures", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });
    const synchronous = new TypeError("decoder rejected the payload");
    const asynchronous = new TypeError("asynchronous decoder failure");
    const syncLoader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load: () => {
        throw synchronous;
      },
    });
    const asyncLoader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load: async () => {
        await Promise.resolve();
        throw asynchronous;
      },
    });

    await expect(
      notebookExport.state("alpha").output("array").load(syncLoader),
    ).rejects.toMatchObject({
      code: "decode_failed",
      cause: synchronous,
      details: { output: "array", codec: "numpy.npy.v1", mediaType: "application/x-npy" },
    });
    await expect(
      notebookExport.state("alpha").output("array").load(asyncLoader),
    ).rejects.toMatchObject({
      code: "decode_failed",
      cause: asynchronous,
      details: { output: "array", codec: "numpy.npy.v1", mediaType: "application/x-npy" },
    });
  });

  test("preserves locally constructed typed loader failures", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });
    const detailsSource = { loader: { name: "primary" } };
    const failure = new NotebookExportError("decode_failed", "Typed decoder failure.", {
      details: detailsSource,
    });
    const loader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load: () => {
        throw failure;
      },
    });

    detailsSource.loader.name = "source changed";
    expect(Object.isFrozen(failure)).toBe(true);
    expect(Object.isFrozen(failure.details)).toBe(true);
    expect(Object.isFrozen(failure.details?.loader)).toBe(true);
    expect(failure.details).toEqual({ loader: { name: "primary" } });
    await expect(notebookExport.state("alpha").output("array").load(loader)).rejects.toBe(failure);
  });

  test("preserves a branded failure across realm and package-copy boundaries", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });
    const foreign = runInNewContext(`(() => {
      const error = new Error("foreign typed failure", { cause: { origin: "iframe" } });
      error.name = "NotebookExportError";
      error.code = "loader_unavailable";
      error.details = { loader: "iframe" };
      Object.defineProperty(
        error,
        Symbol.for("@marimo-team/marimo-export.NotebookExportError.v1"),
        { value: true }
      );
      return error;
    })()`) as unknown;
    class PackageCopyError extends Error {
      readonly code = "loader_invalid";
      readonly details = { packageCopy: true };

      constructor() {
        super("package copy failure");
        this.name = "NotebookExportError";
        Object.defineProperty(this, NOTEBOOK_EXPORT_ERROR_BRAND, { value: true });
      }
    }
    const copied = new PackageCopyError();

    for (const failure of [foreign, copied]) {
      expect(isNotebookExportError(failure)).toBe(true);
      const loader = defineOutputLoader({
        codec: "numpy.npy.v1",
        accepts: () => true,
        load: () => {
          throw failure;
        },
      });
      // The two compatibility cases intentionally cross the same public load boundary.
      // oxlint-disable-next-line no-await-in-loop
      await expect(notebookExport.state("alpha").output("array").load(loader)).rejects.toBe(
        failure,
      );
    }
  });

  test("preserves loader and cross-realm abort causes", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });
    const controller = new AbortController();
    const reason = new Error("superseded state");
    const signalLoader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load: ({ signal }) => {
        controller.abort(reason);
        signal?.throwIfAborted();
      },
    });
    const foreign = runInNewContext(
      'Object.assign(new Error("cross-realm cancellation"), { name: "AbortError" })',
    ) as unknown;
    const foreignLoader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load: () => {
        throw foreign;
      },
    });

    await expect(
      notebookExport.state("alpha").output("array").load(signalLoader, {
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ code: "abort", cause: reason });
    await expect(
      notebookExport.state("alpha").output("array").load(foreignLoader),
    ).rejects.toMatchObject({ code: "abort", cause: foreign });
  });

  test("maps operation and custom-fetch cancellation to the abort contract", async () => {
    const fixture = await exportFixture();
    const controller = new AbortController();
    controller.abort("stop");
    await expect(
      openExport("https://example.test/stocks?file=notebook.py", {
        fetch: fixture.fetch,
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ code: "abort" });

    const cause = Object.freeze({ name: "AbortError", message: "custom fetch cancelled" });
    const fetch: typeof globalThis.fetch = async () => {
      throw cause;
    };
    await expect(openExport("https://example.test/stocks", { fetch })).rejects.toMatchObject({
      code: "abort",
      cause,
    });
  });

  test("cancels a stalled response body after headers arrive", async () => {
    const controller = new AbortController();
    let started: (() => void) | undefined;
    const reading = new Promise<void>((resolve) => {
      started = resolve;
    });
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      pull() {
        started?.();
        return new Promise<void>(() => undefined);
      },
      cancel() {
        cancelled = true;
      },
    });
    const opening = openExport("https://example.test/stocks", {
      fetch: async () => new Response(body),
      signal: controller.signal,
    });
    await reading;

    controller.abort("stop");

    let timeout: ReturnType<typeof setTimeout> | undefined;
    const promptly = Promise.race([
      opening,
      new Promise<never>((_resolve, reject) => {
        timeout = setTimeout(() => reject(new Error("Abort did not stop the body read.")), 250);
      }),
    ]);
    await expect(promptly).rejects.toMatchObject({ code: "abort" });
    if (timeout !== undefined) clearTimeout(timeout);
    expect(cancelled).toBe(true);
  });

  test("keeps the fixed query on an aborted asset request", async () => {
    const fixture = await exportFixture();
    let releaseStarted: (() => void) | undefined;
    const started = new Promise<void>((resolve) => {
      releaseStarted = resolve;
    });
    let assetUrl: string | undefined;
    const fetch: typeof globalThis.fetch = async (input, init) => {
      const url = input instanceof Request ? input.url : input.toString();
      if (!new URL(url).pathname.endsWith(".npy")) return fixture.fetch(input, init);
      assetUrl = url;
      releaseStarted?.();
      return await new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        );
      });
    };
    const notebookExport = await openExport("https://example.test/stocks?file=notebook.py", {
      fetch,
    });
    const loader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load: ({ payload }) => payload,
    });
    const controller = new AbortController();
    const loading = notebookExport
      .state("alpha")
      .output("array")
      .load(loader, { signal: controller.signal });

    await started;
    controller.abort("stop");

    await expect(loading).rejects.toMatchObject({ code: "abort" });
    expect(new URL(assetUrl!).search).toBe("?file=notebook.py");
  });

  test("rejects a successful response without a readable body", async () => {
    await expect(
      openExport("https://example.test/stocks", {
        fetch: async () => new Response(null, { status: 200 }),
      }),
    ).rejects.toMatchObject({ code: "read_failed" });
  });
});
