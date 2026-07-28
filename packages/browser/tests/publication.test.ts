import { encode } from "@msgpack/msgpack";
import { describe, expect, test, vi } from "vite-plus/test";

import type { FormatLoader, MountedView } from "../src/loader.js";
import { openPublication } from "../src/index.js";
import { openPublicationFromSource } from "../src/publication.js";
import { memorySource } from "../src/source.js";
import { PublicationError } from "../src/types.js";
import { digest, fixture, indexFor } from "./fixture.js";
import pythonBlobAsset from "./fixtures/blob-asset-v1.json" with { type: "json" };

const encoder = new TextEncoder();

const PYTHON_BLOB_ASSET = hexBytes(pythonBlobAsset.encoded_hex);

describe("publication reader", () => {
  test("reads a BlobAsset envelope encoded by Python msgspec", async () => {
    const key = "C_python/return.bin";
    const index = indexFor({
      formatId: pythonBlobAsset.format_id,
      mediaType: pythonBlobAsset.media_type,
      metadata: pythonBlobAsset.metadata,
      assetKey: key,
      sha256: pythonBlobAsset.sha256,
      size: PYTHON_BLOB_ASSET.byteLength,
    });
    const fetch: typeof globalThis.fetch = async (input) => {
      const url = input instanceof Request ? input.url : input.toString();
      if (url.endsWith("/index.json")) return new Response(JSON.stringify(index));
      if (url.endsWith(`/cache/${key}`)) {
        return new Response(new Uint8Array(PYTHON_BLOB_ASSET));
      }
      return new Response(null, { status: 404 });
    };

    const publication = await openPublication("https://example.test/export/", { fetch });
    const format = publication.variant("current").output("summary").format("json");

    expect(format.formatId).toBe(pythonBlobAsset.format_id);
    expect(format.mediaType).toBe(pythonBlobAsset.media_type);
    expect(format.metadata).toEqual(pythonBlobAsset.metadata);
    expect(await format.filename()).toBe(pythonBlobAsset.filename);
    expect(await format.bytes()).toEqual(encoder.encode(pythonBlobAsset.data_utf8));
  });

  test.each([
    ["media type", { envelopeMediaType: "\ufeffapplication/json" }],
    [
      "format ID",
      {
        envelopeMetadata: {
          format_id: "\ufeffjson.v1",
          metadata_json: encoder.encode("{}"),
        },
      },
    ],
  ])("preserves a leading BOM in the BlobAsset %s", async (_label, options) => {
    const { publication } = await fixture(options);

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test("preserves a leading BOM in the BlobAsset filename", async () => {
    const filename = "\ufeffsummary.json";
    const { publication } = await fixture({ filename });

    await expect(
      publication.variant("current").output("summary").format("json").filename(),
    ).resolves.toBe(filename);
  });

  test.each([
    ["format ID", { formatId: "a".repeat(255) }],
    ["media type", { mediaType: `application/${"a".repeat(1_012)}` }],
  ])("accepts the BlobAsset %s wire-size boundary", async (_label, options) => {
    const { publication } = await fixture(options);

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).resolves.toEqual(encoder.encode('{"answer":42}'));
  });

  test.each([
    [
      "format ID",
      {
        envelopeMetadata: {
          format_id: "a".repeat(256),
          metadata_json: encoder.encode("{}"),
        },
      },
    ],
    ["media type", { envelopeMediaType: `application/${"a".repeat(1_013)}` }],
  ])("rejects a BlobAsset %s over its wire-size limit", async (_label, options) => {
    const { publication } = await fixture(options);

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test.each([
    ["decimal", (size: number) => `${size}.0`],
    ["exponent", (size: number) => `${size}e0`],
  ])("accepts an integral %s asset size lexeme", async (_label, sizeLexeme) => {
    const built = await fixture();
    const indexText = new TextDecoder()
      .decode(built.indexBytes)
      .replace(
        `"size":${built.envelope.byteLength}`,
        `"size":${sizeLexeme(built.envelope.byteLength)}`,
      );
    const publication = await openPublicationFromSource(
      memorySource({
        "index.json": indexText,
        [`cache/${built.assetKey}`]: built.envelope,
      }),
    );

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).resolves.toEqual(encoder.encode('{"answer":42}'));
  });

  test("accepts an exact integral fractional asset-size lexeme", async () => {
    const indexText = JSON.stringify(indexFor({ size: 15 })).replace('"size":15', '"size":1.5e1');

    await expect(
      openPublicationFromSource(memorySource({ "index.json": indexText })),
    ).resolves.toBeDefined();
  });

  test.each(["1.00000000000000001", "9007199254740990.5", "9007199254740991.1", "1e-324"])(
    "rejects a fractional-loss asset-size lexeme: %s",
    async (lexeme) => {
      const indexText = JSON.stringify(indexFor()).replace('"size":1', `"size":${lexeme}`);

      await expect(
        openPublicationFromSource(memorySource({ "index.json": indexText })),
      ).rejects.toMatchObject({ code: "publication_invalid" });
    },
  );

  test("navigates immutable variants, outputs, and formats", async () => {
    const { publication } = await fixture({ metadata: { rows: 1 } });

    expect(publication.notebook).toEqual({
      filename: "finance.py",
      documentSha256: "a".repeat(64),
    });
    expect(publication.producer).toEqual({ marimo: "0.24.0", marimoExport: "0.0.0" });
    expect(publication.variants().map((variant) => variant.name)).toEqual(["current"]);
    const variant = publication.variant("current");
    const output = variant.output("summary");
    const format = output.format("json");
    expect(format.formatId).toBe("json.v1");
    expect(format.mediaType).toBe("application/json");
    expect(format.metadata).toEqual({ rows: 1 });
    expect(Object.isFrozen(publication)).toBe(true);
    expect(Object.isFrozen(variant.controls)).toBe(true);
    expect(Object.isFrozen(format.metadata)).toBe(true);
  });

  test("orders numeric public names lexically", async () => {
    const base = indexFor();
    const baseOutput = base.variants.current.outputs.summary;
    const baseFormat = baseOutput.formats.json;
    const index = {
      ...base,
      variants: {
        "2": { controls: {}, outputs: { summary: structuredClone(baseOutput) } },
        "10": {
          controls: {},
          outputs: {
            "2": structuredClone(baseOutput),
            "10": {
              formats: {
                "2": structuredClone(baseFormat),
                "10": structuredClone(baseFormat),
              },
            },
          },
        },
        "\ue000": { controls: {}, outputs: { summary: structuredClone(baseOutput) } },
        "\ud800\udc00": { controls: {}, outputs: { summary: structuredClone(baseOutput) } },
      },
    };
    const publication = await openPublicationFromSource(
      memorySource({ "index.json": JSON.stringify(index) }),
    );

    expect(publication.variants().map((item) => item.name)).toEqual([
      "10",
      "2",
      "\ue000",
      "\ud800\udc00",
    ]);
    const numericVariant = publication.variant("10");
    expect(numericVariant.outputs().map((item) => item.name)).toEqual(["10", "2"]);
    expect(
      numericVariant
        .output("10")
        .formats()
        .map((item) => item.name),
    ).toEqual(["10", "2"]);
  });

  test("reads defensive bytes, UTF-8, JSON, Blob, and custom loaders", async () => {
    const data = encoder.encode('{"items":[1,2]}');
    const { publication } = await fixture({ data });
    const format = publication.variant("current").output("summary").format("json");

    const first = await format.bytes();
    first[0] = 0;
    expect(await format.text()).toBe('{"items":[1,2]}');
    expect(await format.json()).toEqual({ items: [1, 2] });
    expect(await format.json((value) => (value as { items: number[] }).items.length)).toBe(2);
    const blob = await format.blob();
    expect(blob.type).toBe("application/json");
    expect(await blob.text()).toBe('{"items":[1,2]}');
    const size = await format.load({
      formatId: "json.v1",
      load: async (context) => {
        expect(context.filename).toBeNull();
        return (await context.bytes()).byteLength;
      },
    });
    expect(size).toBe(data.byteLength);
  });

  test("keeps cancellation authoritative after a custom JSON decoder", async () => {
    const controller = new AbortController();
    const { publication } = await fixture();
    const format = publication.variant("current").output("summary").format("json");

    await expect(
      format.json(
        (value) => {
          controller.abort();
          return value;
        },
        { signal: controller.signal },
      ),
    ).rejects.toMatchObject({ name: "AbortError" });
  });

  test("uses a registered loader to mount and returns one disposal lifecycle", async () => {
    const dispose = vi.fn();
    const loader: FormatLoader<{ readonly answer: number }> = {
      formatId: "json.v1",
      async load(context) {
        return context.json((value) => value as { readonly answer: number });
      },
      async mount(context, element) {
        const value = await context.json((item) => item as { readonly answer: number });
        element.dataset.answer = String(value.answer);
        return { dispose };
      },
    };
    const { publication } = await fixture({ loaders: [loader] });
    const element = { dataset: {} } as unknown as HTMLElement;

    const mounted = await publication
      .variant("current")
      .output("summary")
      .format("json")
      .mount(element);

    expect(element.dataset.answer).toBe("42");
    await mounted.dispose();
    expect(dispose).toHaveBeenCalledOnce();
  });

  test("deduplicates concurrent envelope reads and returns separate byte arrays", async () => {
    const built = await fixture();
    let assetReads = 0;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const source = {
      async read(path: string, options = {}) {
        if (path.startsWith("cache/")) {
          assetReads += 1;
          await gate;
        }
        return built.source.read(path, options);
      },
    };
    const publication = await openPublicationFromSource(source);
    const format = publication.variant("current").output("summary").format("json");

    const first = format.bytes();
    const second = format.bytes();
    await Promise.resolve();
    release();
    const [firstBytes, secondBytes] = await Promise.all([first, second]);

    expect(assetReads).toBe(1);
    expect(firstBytes).toEqual(secondBytes);
    expect(firstBytes).not.toBe(secondBytes);
  });

  test("deduplicates signaled reads while cancelling each caller independently", async () => {
    const built = await fixture();
    let assetReads = 0;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const source = {
      async read(path: string, options = {}) {
        if (path.startsWith("cache/")) {
          assetReads += 1;
          await gate;
        }
        return built.source.read(path, options);
      },
    };
    const publication = await openPublicationFromSource(source);
    const format = publication.variant("current").output("summary").format("json");
    const firstController = new AbortController();
    const secondController = new AbortController();
    const first = format.bytes({ signal: firstController.signal });
    const second = format.bytes({ signal: secondController.signal });
    firstController.abort();
    release();

    await expect(first).rejects.toMatchObject({ name: "AbortError" });
    await expect(second).resolves.toEqual(encoder.encode('{"answer":42}'));
    expect(assetReads).toBe(1);
  });

  test.each([
    ["variant", () => fixture().then(({ publication }) => publication.variant("missing"))],
    [
      "output",
      () => fixture().then(({ publication }) => publication.variant("current").output("missing")),
    ],
    [
      "format",
      () =>
        fixture().then(({ publication }) =>
          publication.variant("current").output("summary").format("missing"),
        ),
    ],
  ])("reports a missing %s through the stable error contract", async (_kind, action) => {
    await expect(action()).rejects.toMatchObject({ code: "not_found" });
  });

  test("bounds and sorts missing-selector details", async () => {
    const base = indexFor();
    const template = base.variants.current;
    const names = Array.from(
      { length: 20 },
      (_, index) => `variant-${String(index).padStart(2, "0")}`,
    );
    const index = {
      ...base,
      variants: Object.fromEntries(
        [...names].reverse().map((name) => [name, structuredClone(template)]),
      ),
    };
    const publication = await openPublicationFromSource(
      memorySource({ "index.json": JSON.stringify(index) }),
    );
    const requested = "😀".repeat(513);

    const error = capturePublicationError(() => publication.variant(requested));

    expect(error.details).toEqual({
      kind: "variant",
      name: "😀".repeat(512),
      name_truncated: true,
      available: names.slice(0, 16),
      available_count: 20,
      available_truncated: true,
    });
  });

  test("bounds and escapes a missing-selector message", async () => {
    const base = indexFor();
    const template = base.variants.current;
    const hostileName = "\u009b".repeat(1_024);
    const publication = await openPublicationFromSource(
      memorySource({
        "index.json": JSON.stringify({
          ...base,
          variants: { [hostileName]: template },
        }),
      }),
    );

    const error = capturePublicationError(() => publication.variant("missing"));

    expect([...error.message]).toHaveLength(4_096);
    expect(error.message).not.toContain("\u009b");
    expect(error.message).toContain("\\u009b");
    expect(error.message.endsWith("...")).toBe(true);
    expect(error.details).toMatchObject({
      available: [hostileName],
      available_count: 1,
      available_truncated: false,
    });
  });

  test("rejects mismatched and unavailable loaders", async () => {
    const { publication } = await fixture();
    const format = publication.variant("current").output("summary").format("json");
    await expect(format.load({ formatId: "text.v1", load: () => "wrong" })).rejects.toMatchObject({
      code: "loader_unavailable",
    });
    await expect(format.mount({} as HTMLElement)).rejects.toMatchObject({
      code: "loader_unavailable",
    });
  });

  test("settles cancellation when a custom loader never resolves", async () => {
    const controller = new AbortController();
    const load = vi.fn(() => new Promise<never>(() => undefined));
    const { publication } = await fixture();
    const loading = publication
      .variant("current")
      .output("summary")
      .format("json")
      .load({ formatId: "json.v1", load }, { signal: controller.signal });
    await vi.waitFor(() => expect(load).toHaveBeenCalledOnce());

    controller.abort();

    await expect(loading).rejects.toMatchObject({ name: "AbortError" });
  });

  test("rejects duplicate registered loaders before publication navigation", async () => {
    const loader: FormatLoader = { formatId: "json.v1", load: () => null };
    await expect(fixture({ loaders: [loader, loader] })).rejects.toThrow("already registered");
  });

  test("rejects a non-callable loader mount hook", async () => {
    const loader = {
      formatId: "json.v1",
      load: () => null,
      mount: "invalid",
    } as unknown as FormatLoader;
    await expect(fixture({ loaders: [loader] })).rejects.toThrow("optional mount function");
  });

  test("rejects a loader format ID outside the publication contract", async () => {
    const loader = { formatId: "json/v1", load: () => null };
    await expect(fixture({ loaders: [loader] })).rejects.toThrow("must define a formatId");
  });

  test("verifies the complete MessagePack envelope before decoding", async () => {
    const built = await fixture();
    const corrupted = new Uint8Array(built.envelope);
    corrupted[corrupted.byteLength - 1] = corrupted[corrupted.byteLength - 1]! ^ 1;
    const source = memorySource({
      "index.json": built.indexBytes,
      [`cache/${built.assetKey}`]: corrupted,
    });
    const publication = await openPublicationFromSource(source);

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "integrity_failed" });
  });

  test("rejects malformed MessagePack after integrity verification", async () => {
    const envelope = Uint8Array.of(0xc1);
    const sha256 = await digest(envelope);
    const index = indexFor({ sha256, size: envelope.byteLength });
    const source = memorySource({
      "index.json": JSON.stringify(index),
      "cache/C_fixture/return.bin": envelope,
    });
    const publication = await openPublicationFromSource(source);

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test.each([new Date(0), Uint8Array.of(0xc0, 0xaf)])(
    "rejects invalid metadata_json %#",
    async (metadataJson) => {
      const { publication } = await fixture({
        envelopeMetadata: { format_id: "json.v1", metadata_json: metadataJson },
      });
      await expect(
        publication.variant("current").output("summary").format("json").bytes(),
      ).rejects.toMatchObject({ code: "asset_invalid" });
    },
  );

  test("rejects numeric MessagePack metadata keys instead of coercing them", async () => {
    const envelope = replaceFinalEmptyMap(
      encode({
        data: encoder.encode("ok"),
        media_type: "application/json",
        filename: null,
        metadata: {},
      }),
      Uint8Array.of(0x81, 0x01, 0xa1, 0x78),
    );
    const { publication } = await fixture({ envelope, metadata: { "1": "x" } });

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test("preserves an own __proto__ metadata key without prototype assignment", async () => {
    const metadata = JSON.parse('{"__proto__":{"safe":true}}') as {
      readonly __proto__: { readonly safe: boolean };
    };
    const { publication } = await fixture({ metadata });
    const format = publication.variant("current").output("summary").format("json");

    await expect(format.bytes()).resolves.toEqual(encoder.encode('{"answer":42}'));
    expect(Object.hasOwn(format.metadata, "__proto__")).toBe(true);
    expect(format.metadata.__proto__).toEqual({ safe: true });
    expect(Object.getPrototypeOf(format.metadata)).toBe(Object.prototype);
  });

  test("preserves a BOM-prefixed __proto__ metadata key exactly", async () => {
    const key = "\uFEFF__proto__";
    const metadata = { [key]: { safe: true } };
    const { publication } = await fixture({ metadata });
    const format = publication.variant("current").output("summary").format("json");

    await expect(format.bytes()).resolves.toEqual(encoder.encode('{"answer":42}'));
    expect(format.metadata[key]).toEqual({ safe: true });
    expect(Object.hasOwn(format.metadata, "__proto__")).toBe(false);
  });

  test("rejects a BOM-prefixed __proto__ key as a metadata mismatch", async () => {
    const metadata = JSON.parse('{"__proto__":{"safe":true}}');
    const envelopeMetadata = {
      format_id: "json.v1",
      metadata_json: encoder.encode(JSON.stringify({ ["\uFEFF__proto__"]: { safe: true } })),
    };
    const { publication } = await fixture({ metadata, envelopeMetadata });

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test("rejects duplicate MessagePack map keys before they are overwritten", async () => {
    const canonical = encode({
      data: encoder.encode("ok"),
      media_type: "application/json",
      filename: null,
      metadata: { format_id: "json.v1", metadata_json: encoder.encode("{}") },
    });
    const envelope = appendDuplicateFixMapEntry(canonical, "data", encoder.encode("ok"));
    const { publication } = await fixture({ envelope });

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test.each([
    [
      "outer",
      encode({
        media_type: "application/json",
        data: encoder.encode("ok"),
        filename: null,
        metadata: { format_id: "json.v1", metadata_json: encoder.encode("{}") },
      }),
    ],
    [
      "metadata",
      encode({
        data: encoder.encode("ok"),
        media_type: "application/json",
        filename: null,
        metadata: { metadata_json: encoder.encode("{}"), format_id: "json.v1" },
      }),
    ],
  ])("rejects a reordered %s MessagePack map", async (_map, envelope) => {
    const { publication } = await fixture({ envelope });

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test("rejects malformed UTF-8 in MessagePack strings", async () => {
    const envelope = replaceFinalEmptyMap(
      encode({
        data: encoder.encode("ok"),
        media_type: "application/json",
        filename: null,
        metadata: {},
      }),
      Uint8Array.of(0x81, 0xa1, 0x78, 0xa2, 0xc0, 0xaf),
    );
    const { publication } = await fixture({ envelope, metadata: { x: "/" } });

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test("rejects disproportionate MessagePack container lengths", async () => {
    const envelope = replaceFinalEmptyMap(
      encode({
        data: encoder.encode("ok"),
        media_type: "application/json",
        filename: null,
        metadata: {},
      }),
      Uint8Array.of(0xdf, 0xff, 0xff, 0xff, 0xff),
    );
    const { publication } = await fixture({ envelope });

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test("rejects MessagePack containers beyond the envelope depth", async () => {
    const nested = Uint8Array.from([
      0x81, 0xa1, 0x78, 0x81, 0xa1, 0x78, 0x81, 0xa1, 0x78, 0x81, 0xa1, 0x78, 0x81, 0xa1, 0x78,
      0xc0,
    ]);
    const envelope = replaceFinalEmptyMap(
      encode({
        data: encoder.encode("ok"),
        media_type: "application/json",
        filename: null,
        metadata: {},
      }),
      nested,
    );
    const { publication } = await fixture({ envelope });

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test("rejects duplicate keys in metadata_json", async () => {
    const { publication } = await fixture({
      metadata: { x: 2 },
      envelopeMetadata: {
        format_id: "json.v1",
        metadata_json: encoder.encode('{"x":1,"x":2}'),
      },
    });

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test("accepts boundary whitespace around BlobAsset metadata without decoding the padding", async () => {
    const padding = new Uint8Array(128 * 1024 + 2);
    padding.fill(0x20);
    padding.set(encoder.encode("{}"), padding.byteLength - 2);
    const { publication } = await fixture({
      envelopeMetadata: {
        format_id: "json.v1",
        metadata_json: padding,
      },
    });

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).resolves.toEqual(encoder.encode('{"answer":42}'));
  });

  test("rejects BlobAsset metadata_json over 256 KiB", async () => {
    const metadataJson = new Uint8Array(256 * 1024 + 1);
    metadataJson.fill(0x20);
    const { publication } = await fixture({
      envelopeMetadata: { format_id: "json.v1", metadata_json: metadataJson },
    });

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test("accepts BlobAsset metadata_json at 256 KiB", async () => {
    const metadata = { value: "x".repeat(256 * 1024 - 12) };
    const { publication } = await fixture({ metadata });

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).resolves.toEqual(encoder.encode('{"answer":42}'));
  });

  test("rejects duplicate keys in indexes and projected JSON", async () => {
    const indexText = JSON.stringify(indexFor()).replace(
      '"metadata":{}',
      '"metadata":{"x":1,"\\u0078":2}',
    );
    await expect(
      openPublicationFromSource(memorySource({ "index.json": indexText })),
    ).rejects.toMatchObject({ code: "publication_invalid" });

    const { publication } = await fixture({ data: encoder.encode('{"x":1,"\\u0078":2}') });
    const format = publication.variant("current").output("summary").format("json");
    await expect(format.json()).rejects.toMatchObject({ code: "decode_failed" });
    await expect(
      format.load({ formatId: "json.v1", load: (context) => context.json() }),
    ).rejects.toMatchObject({ code: "decode_failed" });
  });

  test("bounds projected JSON and accepts an explicit override", async () => {
    const data = encoder.encode(`[${Array<string>(100_000).fill("null").join(",")}]`);
    const { publication } = await fixture({ data });
    const format = publication.variant("current").output("summary").format("json");

    await expect(format.json()).rejects.toMatchObject({ code: "decode_failed" });
    await expect(format.json({ maxJsonValues: 100_001 })).resolves.toHaveLength(100_000);
  });

  test("preserves signed zero in projected JSON", async () => {
    const { publication } = await fixture({ data: encoder.encode("[-0,-0.0]") });
    const value = await publication.variant("current").output("summary").format("json").json();

    expect(Array.isArray(value)).toBe(true);
    expect(Object.is((value as readonly unknown[])[0], -0)).toBe(true);
    expect(Object.is((value as readonly unknown[])[1], -0)).toBe(true);
  });

  test("passes the projected JSON limit to loader contexts", async () => {
    const { publication } = await fixture({ data: encoder.encode("[null]") });
    const format = publication.variant("current").output("summary").format("json");
    const loader: FormatLoader = {
      formatId: "json.v1",
      load: (context) => context.json(),
    };

    await expect(format.load(loader, { maxJsonValues: 1 })).rejects.toMatchObject({
      code: "decode_failed",
    });
    await expect(format.load(loader, { maxJsonValues: 2 })).resolves.toEqual([null]);
  });

  test.each([0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])(
    "rejects the invalid projected JSON limit %s",
    async (maxJsonValues) => {
      const { publication } = await fixture();
      const format = publication.variant("current").output("summary").format("json");

      await expect(format.json({ maxJsonValues })).rejects.toThrow(TypeError);
    },
  );

  test("rejects a UTF-8 BOM in indexes and projected JSON", async () => {
    const index = encoder.encode(JSON.stringify(indexFor()));
    const indexWithBom = new Uint8Array(index.byteLength + 3);
    indexWithBom.set([0xef, 0xbb, 0xbf]);
    indexWithBom.set(index, 3);
    await expect(
      openPublicationFromSource(memorySource({ "index.json": indexWithBom })),
    ).rejects.toMatchObject({ code: "publication_invalid" });

    const data = encoder.encode('{"answer":42}');
    const dataWithBom = new Uint8Array(data.byteLength + 3);
    dataWithBom.set([0xef, 0xbb, 0xbf]);
    dataWithBom.set(data, 3);
    const { publication } = await fixture({ data: dataWithBom });
    const format = publication.variant("current").output("summary").format("json");
    await expect(format.text()).resolves.toBe('{"answer":42}');
    await expect(format.json()).rejects.toMatchObject({ code: "decode_failed" });
  });

  test("accepts an explicit UTF-8 charset", async () => {
    const { publication } = await fixture({
      data: encoder.encode("plain text"),
      mediaType: 'text/plain; charset="UTF-8"',
    });
    const format = publication.variant("current").output("summary").format("json");

    await expect(format.text()).resolves.toBe("plain text");
  });

  test.each(["text/plain; charset=", 'text/plain; charset=""', 'text/plain; charset=" utf-8 "'])(
    "rejects an invalid declared charset in %s",
    async (mediaType) => {
      const { publication } = await fixture({
        data: encoder.encode("plain text"),
        mediaType,
      });
      const format = publication.variant("current").output("summary").format("json");

      await expect(format.text()).rejects.toMatchObject({ code: "decode_failed" });
    },
  );

  test.each([
    ["ISO-8859-1", "iso-8859-1", Uint8Array.of(0x63, 0x61, 0x66, 0xe9)],
    ["UTF-16 with a big-endian BOM", "utf-16", Uint8Array.of(0xfe, 0xff, 0x00, 0x41)],
  ])("rejects %s projected text", async (_label, charset, data) => {
    const { publication } = await fixture({
      data,
      mediaType: `text/plain; charset=${charset}`,
    });
    const format = publication.variant("current").output("summary").format("json");

    await expect(format.text()).rejects.toMatchObject({ code: "decode_failed" });
  });

  test.each([
    ["media type", { envelopeMediaType: "text/plain" }],
    [
      "format id",
      {
        envelopeMetadata: {
          format_id: "text.v1",
          metadata_json: encoder.encode("{}"),
        },
      },
    ],
    [
      "metadata",
      {
        envelopeMetadata: {
          format_id: "json.v1",
          metadata_json: encoder.encode('{"other":true}'),
        },
      },
    ],
  ])("rejects a BlobAsset %s mismatch", async (_label, options) => {
    const { publication } = await fixture(options);
    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test("requires the exact BlobAsset map", async () => {
    const envelope = encode({
      data: encoder.encode("ok"),
      media_type: "application/json",
      filename: null,
      metadata: { format_id: "json.v1", metadata_json: encoder.encode("{}") },
      extra: true,
    });
    const { publication } = await fixture({ envelope });

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test.each([
    "",
    ".",
    "..",
    " summary.json",
    "nested/output.json",
    "nested\\output.json",
    "bad\0name",
    "bad\nname",
    "bad\u007fname",
    "bad\ud800name",
    "report.json:secret",
    "report?.json",
    "CON.json",
    "LPT².json",
    "CONIN$.json",
    "report.json.",
    `${"a".repeat(252)}.bin`,
    `${"é".repeat(126)}.bin`,
  ])("rejects the non-portable BlobAsset filename %j", async (filename) => {
    const { publication } = await fixture({ filename });
    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "asset_invalid" });
  });

  test.each([`${"a".repeat(251)}.bin`, `${"é".repeat(125)}a.bin`])(
    "accepts the 255-byte BlobAsset filename %j",
    async (filename) => {
      const { publication } = await fixture({ filename });

      await expect(
        publication.variant("current").output("summary").format("json").filename(),
      ).resolves.toBe(filename);
    },
  );

  test("enforces decoded and envelope byte limits", async () => {
    const built = await fixture({ data: encoder.encode("0123456789") });
    const format = built.publication.variant("current").output("summary").format("json");
    await expect(format.bytes({ maxBytes: 4 })).rejects.toMatchObject({
      code: "read_limit_exceeded",
    });
    await expect(
      openPublicationFromSource(built.source, { assetLimit: built.envelope.byteLength - 1 }).then(
        (publication) => publication.variant("current").output("summary").format("json").bytes(),
      ),
    ).rejects.toMatchObject({ code: "read_limit_exceeded" });
  });

  test("applies the browser envelope limit before fetching an asset", async () => {
    const index = indexFor({ size: 64 * 1024 * 1024 + 1 });
    const publication = await openPublicationFromSource(
      memorySource({ "index.json": JSON.stringify(index) }),
    );

    await expect(
      publication.variant("current").output("summary").format("json").bytes(),
    ).rejects.toMatchObject({ code: "read_limit_exceeded" });
  });

  test("validates a decoded byte limit before reading the asset", async () => {
    const built = await fixture();
    let assetReads = 0;
    const source = {
      async read(path: string, options = {}) {
        if (path.startsWith("cache/")) assetReads += 1;
        return built.source.read(path, options);
      },
    };
    const publication = await openPublicationFromSource(source);

    await expect(
      publication.variant("current").output("summary").format("json").bytes({ maxBytes: -1 }),
    ).rejects.toThrow(TypeError);
    expect(assetReads).toBe(0);
  });

  test.each(["maxIndexBytes", "maxAssetBytes"] as const)(
    "rejects a zero %s limit before network I/O",
    async (name) => {
      const fetch = vi.fn<typeof globalThis.fetch>();

      await expect(
        openPublication("https://example.test/export/", { fetch, [name]: 0 }),
      ).rejects.toThrow(TypeError);
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  test("propagates cancellation through source reads", async () => {
    const built = await fixture();
    const controller = new AbortController();
    const publication = await openPublicationFromSource(built.source);
    controller.abort();
    await expect(
      publication
        .variant("current")
        .output("summary")
        .format("json")
        .bytes({ signal: controller.signal }),
    ).rejects.toMatchObject({ name: "AbortError" });
  });

  test("validates mounted views returned by loaders", async () => {
    const loader = {
      formatId: "json.v1",
      load: () => null,
      mount: () => ({}) as MountedView,
    };
    const { publication } = await fixture({ loaders: [loader] });
    await expect(
      publication
        .variant("current")
        .output("summary")
        .format("json")
        .mount({} as HTMLElement),
    ).rejects.toMatchObject({ code: "loader_unavailable" });
  });

  test("keeps cancellation authoritative during mounted-view validation", async () => {
    const controller = new AbortController();
    const mounted = Object.create(null) as MountedView;
    Object.defineProperty(mounted, "dispose", {
      get() {
        controller.abort();
        return undefined;
      },
    });
    const loader: FormatLoader = {
      formatId: "json.v1",
      load: () => null,
      mount: () => mounted,
    };
    const { publication } = await fixture({ loaders: [loader] });

    await expect(
      publication
        .variant("current")
        .output("summary")
        .format("json")
        .mount({} as HTMLElement, { signal: controller.signal }),
    ).rejects.toMatchObject({ name: "AbortError" });
  });

  test("disposes a mounted view when cancellation wins the mount race", async () => {
    const controller = new AbortController();
    const dispose = vi.fn();
    const loader: FormatLoader = {
      formatId: "json.v1",
      load: () => null,
      mount() {
        controller.abort();
        return { dispose };
      },
    };
    const { publication } = await fixture({ loaders: [loader] });

    await expect(
      publication
        .variant("current")
        .output("summary")
        .format("json")
        .mount({} as HTMLElement, { signal: controller.signal }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(dispose).toHaveBeenCalledOnce();
  });

  test("settles cancellation when mounted-view disposal never resolves", async () => {
    const controller = new AbortController();
    const dispose = vi.fn(() => new Promise<void>(() => undefined));
    const mounted = Object.create(null) as MountedView;
    Object.defineProperty(mounted, "dispose", {
      get() {
        controller.abort();
        return dispose;
      },
    });
    const loader: FormatLoader = {
      formatId: "json.v1",
      load: () => null,
      mount: () => mounted,
    };
    const { publication } = await fixture({ loaders: [loader] });

    await expect(
      publication
        .variant("current")
        .output("summary")
        .format("json")
        .mount({} as HTMLElement, { signal: controller.signal }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(dispose).toHaveBeenCalledOnce();
  });

  test("settles cancellation when a loader mount never resolves", async () => {
    const controller = new AbortController();
    const mount = vi.fn(() => new Promise<MountedView>(() => undefined));
    const loader: FormatLoader = {
      formatId: "json.v1",
      load: () => null,
      mount,
    };
    const { publication } = await fixture({ loaders: [loader] });
    const mounting = publication
      .variant("current")
      .output("summary")
      .format("json")
      .mount({} as HTMLElement, { signal: controller.signal });
    await vi.waitFor(() => expect(mount).toHaveBeenCalledOnce());

    controller.abort();

    await expect(mounting).rejects.toMatchObject({ name: "AbortError" });
  });

  test("disposes a mounted view that resolves after cancellation", async () => {
    const controller = new AbortController();
    const dispose = vi.fn();
    let resolveMount!: (mounted: MountedView) => void;
    const mount = vi.fn(
      () =>
        new Promise<MountedView>((resolve) => {
          resolveMount = resolve;
        }),
    );
    const loader: FormatLoader = {
      formatId: "json.v1",
      load: () => null,
      mount,
    };
    const { publication } = await fixture({ loaders: [loader] });
    const mounting = publication
      .variant("current")
      .output("summary")
      .format("json")
      .mount({} as HTMLElement, { signal: controller.signal });
    await vi.waitFor(() => expect(mount).toHaveBeenCalledOnce());

    controller.abort();
    await expect(mounting).rejects.toMatchObject({ name: "AbortError" });
    resolveMount({ dispose });

    await vi.waitFor(() => expect(dispose).toHaveBeenCalledOnce());
  });

  test("uses PublicationError for stable reader failures", () => {
    const error = new PublicationError("not_found", "missing", { details: { kind: "output" } });
    expect(error.name).toBe("PublicationError");
    expect(error.code).toBe("not_found");
    expect(error.details).toEqual({ kind: "output" });
    expect(Object.isFrozen(error.details)).toBe(true);
  });
});

function hexBytes(value: string): Uint8Array {
  return Uint8Array.from(value.match(/.{2}/g) ?? [], (byte) => Number.parseInt(byte, 16));
}

function capturePublicationError(action: () => unknown): PublicationError {
  try {
    action();
  } catch (error) {
    if (error instanceof PublicationError) return error;
    throw error;
  }
  throw new Error("Expected a PublicationError.");
}

function replaceFinalEmptyMap(envelope: Uint8Array, replacement: Uint8Array): Uint8Array {
  expect(envelope.at(-1)).toBe(0x80);
  const value = new Uint8Array(envelope.byteLength - 1 + replacement.byteLength);
  value.set(envelope.subarray(0, -1));
  value.set(replacement, envelope.byteLength - 1);
  return value;
}

function appendDuplicateFixMapEntry(map: Uint8Array, key: string, value: Uint8Array): Uint8Array {
  expect(map[0]).toBeGreaterThanOrEqual(0x80);
  expect(map[0]).toBeLessThan(0x8f);
  const encodedKey = encode(key);
  const encodedValue = encode(value);
  const result = new Uint8Array(map.byteLength + encodedKey.byteLength + encodedValue.byteLength);
  result.set(map);
  result[0] = map[0]! + 1;
  result.set(encodedKey, map.byteLength);
  result.set(encodedValue, map.byteLength + encodedKey.byteLength);
  return result;
}
