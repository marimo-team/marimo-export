import { describe, expect, test, vi } from "vite-plus/test";

import {
  NotebookExportError,
  defineBlobAssetLoader,
  defineOutputLoader,
  openExport,
  scalarLoader,
} from "../src/index.js";
import { canonicalJson } from "../src/schema.js";
import { exportFixture } from "./fixture.js";

const encoder = new TextEncoder();

describe("export", () => {
  test("opens only the canonical index and exposes immutable exact states", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });

    expect(notebookExport.base.href).toBe("https://example.test/stocks/");
    expect(fixture.requests).toEqual(["https://example.test/stocks/index.json"]);
    expect(notebookExport.notebook).toEqual({
      filename: "finance.py",
      documentSha256: "a".repeat(64),
    });
    expect(notebookExport.producer).toEqual({ marimo: "0.23.15", marimoExport: "1.0.0" });
    expect(notebookExport.inputNames).toEqual(["symbol", "width"]);
    expect(notebookExport.outputNames).toEqual(["count", "array", "table", "view"]);
    expect(notebookExport.states().map((state) => state.name)).toEqual(["alpha", "zeta"]);
    expect(
      notebookExport
        .state("alpha")
        .outputs()
        .map((output) => output.name),
    ).toEqual(["count", "array", "table", "view"]);
    expect(Object.isFrozen(notebookExport)).toBe(true);
    expect(Object.isFrozen(notebookExport.state("alpha"))).toBe(true);
    expect(Object.isFrozen(notebookExport.state("alpha").inputs)).toBe(true);
    expect(Object.isFrozen(notebookExport.state("alpha").output("view").descriptor)).toBe(true);
  });

  test("resolves complete vectors and sparse immutable patches", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport(new URL("https://example.test/stocks/"), {
      fetch: fixture.fetch,
    });
    const alpha = notebookExport.state("alpha");

    expect(notebookExport.resolve({ symbol: "AAPL", width: 800 })).toBe(alpha);
    expect(alpha.resolve({})).toBe(alpha);
    expect(alpha.resolve({ symbol: "MSFT", width: 640 })).toBe(notebookExport.state("zeta"));
    expect(alpha.inputs).toEqual({ symbol: "AAPL", width: 800 });
    expect(() => notebookExport.resolve({ symbol: "AAPL" })).toThrowError(
      expect.objectContaining({ code: "state_input_invalid" }),
    );
    expect(() => alpha.resolve({ missing: true })).toThrowError(
      expect.objectContaining({ code: "state_input_invalid" }),
    );
    expect(() => alpha.resolve({ symbol: "GOOGL" })).toThrowError(
      expect.objectContaining({ code: "state_unavailable" }),
    );
  });

  test("reports bounded state and output lookup errors", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });

    expect(() => notebookExport.state("missing")).toThrowError(
      expect.objectContaining({ code: "state_not_found" }),
    );
    expect(() => notebookExport.state("alpha").output("missing")).toThrowError(
      expect.objectContaining({ code: "output_not_found" }),
    );
  });

  test("loads scalars without an asset request", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });

    await expect(notebookExport.state("alpha").output("count").load(scalarLoader())).resolves.toBe(
      1,
    );
    expect(fixture.requests).toEqual(["https://example.test/stocks/index.json"]);
  });

  test("returns detached base URLs that cannot redirect asset reads", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });
    const leaked = notebookExport.base;
    leaked.pathname = "/elsewhere/";
    const loader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load: ({ payload }) => payload,
    });

    await notebookExport.state("alpha").output("array").load(loader);

    expect(notebookExport.base.href).toBe("https://example.test/stocks/");
    expect(fixture.requests[1]).toMatch(
      /^https:\/\/example\.test\/stocks\/assets\/[0-9a-f]{64}\.npy$/u,
    );
  });

  test("loads verified native bytes and decoded BlobAssets through explicit loaders", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });
    const bytesLoader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: (_descriptor, mediaType) => mediaType.essence === "application/x-npy",
      load: ({ payload }) => payload,
    });
    const blobLoader = defineBlobAssetLoader({
      mediaTypes: "application/vnd.example.fixture+json",
      load: ({ payload }) => ({
        text: new TextDecoder().decode(payload.data),
        filename: payload.filename,
        metadata: payload.metadata,
      }),
    });

    await expect(notebookExport.state("alpha").output("array").load(bytesLoader)).resolves.toEqual(
      new Uint8Array([0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59, 0x01, 0x00]),
    );
    await expect(notebookExport.state("alpha").output("view").load(blobLoader)).resolves.toEqual({
      text: '{"ready":true}',
      filename: "fixture.json",
      metadata: { representation: "fixture" },
    });
  });

  test("verifies each content-addressed asset once", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });

    await expect(notebookExport.verify()).resolves.toEqual({
      states: 2,
      outputs: 8,
      assets: 3,
      bytesVerified: [...fixture.assets.values()].reduce((sum, value) => sum + value.byteLength, 0),
    });
    expect(fixture.requests).toHaveLength(4);
  });

  test("rejects integrity failures before invoking a loader", async () => {
    const fixture = await exportFixture();
    const brokenFetch: typeof globalThis.fetch = async (input, init) => {
      const response = await fixture.fetch(input, init);
      const url = input instanceof Request ? input.url : input.toString();
      if (url.endsWith(".npy")) {
        const bytes = new Uint8Array(await response.arrayBuffer());
        bytes[bytes.length - 1] = bytes[bytes.length - 1]! ^ 1;
        return new Response(bytes);
      }
      return response;
    };
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: brokenFetch,
    });
    const load = vi.fn(({ payload }: { payload: Uint8Array }) => payload);
    const loader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load,
    });

    await expect(notebookExport.state("alpha").output("array").load(loader)).rejects.toMatchObject({
      code: "integrity_failed",
    });
    expect(load).not.toHaveBeenCalled();
  });

  test("enforces caller byte and aggregate limits before fetching assets", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });
    const loader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load: ({ payload }) => payload,
    });

    await expect(
      notebookExport.state("alpha").output("array").load(loader, { maxBytes: 1 }),
    ).rejects.toMatchObject({ code: "read_limit_exceeded" });
    await expect(notebookExport.verify({ maxTotalBytes: 1 })).rejects.toMatchObject({
      code: "read_limit_exceeded",
    });
    expect(fixture.requests).toEqual(["https://example.test/stocks/index.json"]);
  });

  test("maps cancellation to the export abort contract", async () => {
    const fixture = await exportFixture();
    const controller = new AbortController();
    controller.abort("stop");

    await expect(
      openExport("https://example.test/stocks", {
        fetch: fixture.fetch,
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ code: "abort" });
  });

  test("rejects a successful response without a readable body", async () => {
    await expect(
      openExport("https://example.test/stocks", {
        fetch: async () => new Response(null, { status: 200 }),
      }),
    ).rejects.toMatchObject({ code: "read_failed" });
  });
});

describe("canonical export validation", () => {
  test("rejects whitespace, duplicate keys, and a wrong fingerprint", async () => {
    const fixture = await exportFixture();
    const encodings = [
      encoder.encode(`${new TextDecoder().decode(fixture.indexBytes)}\n`),
      encoder.encode(
        new TextDecoder()
          .decode(fixture.indexBytes)
          .replace('{"inputs":', '{"schema":"duplicate","inputs":'),
      ),
    ];
    for (const bytes of encodings) {
      const fetch: typeof globalThis.fetch = async () => new Response(bytes);
      // Validation cases intentionally execute in order.
      // oxlint-disable-next-line no-await-in-loop
      await expect(openExport("https://example.test/stocks", { fetch })).rejects.toBeInstanceOf(
        NotebookExportError,
      );
    }

    const wrong = structuredClone(fixture.index);
    const states = wrong.states as Record<string, Record<string, unknown>>;
    states.alpha!.fingerprint = "f".repeat(64);
    const bytes = encoder.encode(canonicalJson(wrong as never));
    await expect(
      openExport("https://example.test/stocks", {
        fetch: async () => new Response(bytes),
      }),
    ).rejects.toMatchObject({ code: "export_invalid" });
  });

  test("rejects a representation that changes across states", async () => {
    const fixture = await exportFixture({
      indexTransform(index) {
        const states = index.states as Record<string, Record<string, unknown>>;
        const zeta = states.zeta!.outputs as Record<string, Record<string, unknown>>;
        zeta.view!.media_type = "application/vnd.example.other+json";
      },
    });

    await expect(
      openExport("https://example.test/stocks", { fetch: fixture.fetch }),
    ).rejects.toMatchObject({ code: "output_representation_changed" });
  });
});
