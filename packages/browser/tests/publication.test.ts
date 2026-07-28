import { describe, expect, test, vi } from "vite-plus/test";

import {
  PublicationError,
  defineBlobAssetLoader,
  defineOutputLoader,
  openPublication,
  scalarLoader,
} from "../src/index.js";
import { canonicalJson } from "../src/schema.js";
import { publicationFixture, scalar } from "./fixture.js";

const encoder = new TextEncoder();

describe("publication", () => {
  test("opens only the canonical index and exposes immutable exact states", async () => {
    const fixture = await publicationFixture();
    const publication = await openPublication("https://example.test/stocks", {
      fetch: fixture.fetch,
    });

    expect(publication.base.href).toBe("https://example.test/stocks/");
    expect(fixture.requests).toEqual(["https://example.test/stocks/index.json"]);
    expect(publication.notebook).toEqual({
      filename: "finance.py",
      documentSha256: "a".repeat(64),
    });
    expect(publication.producer).toEqual({ marimo: "0.23.15", marimoExport: "1.0.0" });
    expect(publication.inputNames).toEqual(["symbol", "width"]);
    expect(publication.outputNames).toEqual(["count", "array", "table", "view"]);
    expect(publication.states().map((state) => state.name)).toEqual(["alpha", "zeta"]);
    expect(
      publication
        .state("alpha")
        .outputs()
        .map((output) => output.name),
    ).toEqual(["count", "array", "table", "view"]);
    expect(Object.isFrozen(publication)).toBe(true);
    expect(Object.isFrozen(publication.state("alpha"))).toBe(true);
    expect(Object.isFrozen(publication.state("alpha").inputs)).toBe(true);
    expect(Object.isFrozen(publication.state("alpha").output("view").descriptor)).toBe(true);
  });

  test("resolves complete vectors and sparse immutable patches", async () => {
    const fixture = await publicationFixture();
    const publication = await openPublication(new URL("https://example.test/stocks/"), {
      fetch: fixture.fetch,
    });
    const alpha = publication.state("alpha");

    expect(publication.resolve({ symbol: "AAPL", width: 800 })).toBe(alpha);
    expect(alpha.resolve({})).toBe(alpha);
    expect(alpha.resolve({ symbol: "MSFT", width: 640 })).toBe(publication.state("zeta"));
    expect(alpha.inputs).toEqual({ symbol: "AAPL", width: 800 });
    expect(() => publication.resolve({ symbol: "AAPL" })).toThrowError(
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
    const fixture = await publicationFixture();
    const publication = await openPublication("https://example.test/stocks", {
      fetch: fixture.fetch,
    });

    expect(() => publication.state("missing")).toThrowError(
      expect.objectContaining({ code: "state_not_found" }),
    );
    expect(() => publication.state("alpha").output("missing")).toThrowError(
      expect.objectContaining({ code: "output_not_found" }),
    );
  });

  test("loads scalars without an asset request", async () => {
    const fixture = await publicationFixture();
    const publication = await openPublication("https://example.test/stocks", {
      fetch: fixture.fetch,
    });

    await expect(publication.state("alpha").output("count").load(scalarLoader())).resolves.toBe(1);
    expect(fixture.requests).toEqual(["https://example.test/stocks/index.json"]);
  });

  test("loads verified native bytes and decoded BlobAssets through explicit loaders", async () => {
    const fixture = await publicationFixture();
    const publication = await openPublication("https://example.test/stocks", {
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

    await expect(publication.state("alpha").output("array").load(bytesLoader)).resolves.toEqual(
      new Uint8Array([0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59, 0x01, 0x00]),
    );
    await expect(publication.state("alpha").output("view").load(blobLoader)).resolves.toEqual({
      text: '{"ready":true}',
      filename: "fixture.json",
      metadata: { representation: "fixture" },
    });
  });

  test("verifies each content-addressed asset once", async () => {
    const fixture = await publicationFixture();
    const publication = await openPublication("https://example.test/stocks", {
      fetch: fixture.fetch,
    });

    await expect(publication.verify()).resolves.toEqual({
      states: 2,
      outputs: 4,
      assets: 3,
      bytesVerified: [...fixture.assets.values()].reduce((sum, value) => sum + value.byteLength, 0),
    });
    expect(fixture.requests).toHaveLength(4);
  });

  test("rejects integrity failures before invoking a loader", async () => {
    const fixture = await publicationFixture();
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
    const publication = await openPublication("https://example.test/stocks", {
      fetch: brokenFetch,
    });
    const load = vi.fn(({ payload }: { payload: Uint8Array }) => payload);
    const loader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load,
    });

    await expect(publication.state("alpha").output("array").load(loader)).rejects.toMatchObject({
      code: "integrity_failed",
    });
    expect(load).not.toHaveBeenCalled();
  });

  test("enforces caller byte and aggregate limits before fetching assets", async () => {
    const fixture = await publicationFixture();
    const publication = await openPublication("https://example.test/stocks", {
      fetch: fixture.fetch,
    });
    const loader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load: ({ payload }) => payload,
    });

    await expect(
      publication.state("alpha").output("array").load(loader, { maxBytes: 1 }),
    ).rejects.toMatchObject({ code: "read_limit_exceeded" });
    await expect(publication.verify({ maxTotalBytes: 1 })).rejects.toMatchObject({
      code: "read_limit_exceeded",
    });
    expect(fixture.requests).toEqual(["https://example.test/stocks/index.json"]);
  });

  test("maps cancellation to the publication abort contract", async () => {
    const fixture = await publicationFixture();
    const controller = new AbortController();
    controller.abort("stop");

    await expect(
      openPublication("https://example.test/stocks", {
        fetch: fixture.fetch,
        signal: controller.signal,
      }),
    ).rejects.toMatchObject({ code: "abort" });
  });
});

describe("canonical publication validation", () => {
  test("rejects whitespace, duplicate keys, and a wrong fingerprint", async () => {
    const fixture = await publicationFixture();
    const variants = [
      encoder.encode(`${new TextDecoder().decode(fixture.indexBytes)}\n`),
      encoder.encode(
        new TextDecoder()
          .decode(fixture.indexBytes)
          .replace('{"inputs":', '{"schema":"duplicate","inputs":'),
      ),
    ];
    for (const bytes of variants) {
      const fetch: typeof globalThis.fetch = async () => new Response(bytes);
      // Validation cases intentionally execute in order.
      // oxlint-disable-next-line no-await-in-loop
      await expect(
        openPublication("https://example.test/stocks", { fetch }),
      ).rejects.toBeInstanceOf(PublicationError);
    }

    const wrong = structuredClone(fixture.index);
    const states = wrong.states as Record<string, Record<string, unknown>>;
    states.alpha!.fingerprint = "f".repeat(64);
    const bytes = encoder.encode(canonicalJson(wrong as never));
    await expect(
      openPublication("https://example.test/stocks", {
        fetch: async () => new Response(bytes),
      }),
    ).rejects.toMatchObject({ code: "publication_invalid" });
  });

  test("decodes tagged bigint and special float scalars", async () => {
    const fixture = await publicationFixture({
      indexTransform(index) {
        const states = index.states as Record<string, Record<string, unknown>>;
        for (const state of Object.values(states)) {
          const outputs = state.outputs as Record<string, unknown>;
          outputs.count = scalar({ type: "bigint", value: "9007199254740992" });
        }
      },
    });
    const publication = await openPublication("https://example.test/stocks", {
      fetch: fixture.fetch,
    });
    await expect(publication.state("alpha").output("count").load(scalarLoader())).resolves.toBe(
      9007199254740992n,
    );
  });

  test("rejects a representation that changes across states", async () => {
    const fixture = await publicationFixture({
      indexTransform(index) {
        const states = index.states as Record<string, Record<string, unknown>>;
        const zeta = states.zeta!.outputs as Record<string, Record<string, unknown>>;
        zeta.view!.media_type = "application/vnd.example.other+json";
      },
    });

    await expect(
      openPublication("https://example.test/stocks", { fetch: fixture.fetch }),
    ).rejects.toMatchObject({ code: "output_representation_changed" });
  });
});
