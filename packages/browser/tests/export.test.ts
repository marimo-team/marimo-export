import { describe, expect, test, vi } from "vite-plus/test";

import {
  NotebookExportError,
  defineBlobAssetLoader,
  defineOutputLoader,
  openExport,
  scalarLoader,
} from "../src/index.js";
import { canonicalJson } from "../src/schema.js";
import { sha256Hex } from "../src/integrity.js";
import { exportFixture, mutableObject, stringValue } from "./fixture.js";

const encoder = new TextEncoder();

describe("export", () => {
  test("opens only the canonical index and exposes immutable exact states", async () => {
    const fixture = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });

    expect(notebookExport.base.href).toBe("https://example.test/stocks/");
    expect(notebookExport.identity).toBe(await sha256Hex(fixture.indexBytes));
    expect(notebookExport.specSha256).toBe("d".repeat(64));
    expect(notebookExport.defaultState).toBe(notebookExport.state("alpha"));
    expect(fixture.requests).toEqual(["https://example.test/stocks/index.json"]);
    expect(notebookExport.notebook).toEqual({
      filename: "finance.py",
      documentSha256: "a".repeat(64),
    });
    expect(notebookExport.producer).toEqual({
      implementationSha256: "c".repeat(64),
      marimo: "0.23.15",
      marimoExport: "1.0.0",
    });
    expect(notebookExport.inputNames).toEqual(["symbol", "width"]);
    expect(notebookExport.controlBindings).toEqual({
      "cell-symbol-0": { input: "symbol", path: [] },
    });
    expect(Object.isFrozen(notebookExport.controlBindings)).toBe(true);
    expect(Object.isFrozen(notebookExport.controlBindings["cell-symbol-0"])).toBe(true);
    expect(Object.isFrozen(notebookExport.controlBindings["cell-symbol-0"]!.path)).toBe(true);
    expect(notebookExport.outputNames).toEqual(["count", "array", "table", "view"]);
    expect(notebookExport.states().map((state) => state.fingerprint)).toEqual(
      Object.keys(mutableObject(fixture.index.states, "states")).sort(),
    );
    expect(notebookExport.state("alpha").aliases).toEqual(["alpha", "first"]);
    expect(notebookExport.state("first")).toBe(notebookExport.state("alpha"));
    expect(notebookExport.state("alpha").output("count").descriptor.provenance).toEqual({
      pythonType: "fixture.Value",
    });
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
    const notebookExport = await openExport("https://example.test/stocks?file=notebook.py", {
      fetch: fixture.fetch,
    });
    const leaked = notebookExport.base;
    leaked.pathname = "/elsewhere/";
    leaked.search = "?file=elsewhere.py";
    const loader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load: ({ payload }) => payload,
    });

    await notebookExport.state("alpha").output("array").load(loader);

    expect(notebookExport.base.href).toBe("https://example.test/stocks/?file=notebook.py");
    expect(fixture.requests[1]).toMatch(
      /^https:\/\/example\.test\/stocks\/assets\/[0-9a-f]{64}\.npy\?file=notebook\.py$/u,
    );
  });

  test("preserves a fixed query across nested index and asset reads", async () => {
    const basePath = "/api/files/exports/nested/";
    const fixture = await exportFixture({ basePath });
    const notebookExport = await openExport(
      "https://example.test/api/files/exports/nested?file=folder%2Fnotebook.py&mode=read",
      { fetch: fixture.fetch },
    );
    const loader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load: ({ payload }) => payload,
    });

    await notebookExport.state("alpha").output("array").load(loader);

    expect(notebookExport.base.href).toBe(
      "https://example.test/api/files/exports/nested/?file=folder%2Fnotebook.py&mode=read",
    );
    expect(fixture.requests).toHaveLength(2);
    for (const request of fixture.requests) {
      const url = new URL(request);
      expect(url.pathname.startsWith(basePath)).toBe(true);
      expect(url.search).toBe("?file=folder%2Fnotebook.py&mode=read");
    }
  });

  test("rejects an export base fragment", async () => {
    await expect(openExport("https://example.test/stocks#section")).rejects.toThrow(
      /must not contain a fragment/u,
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

  test("reuses a content-addressed asset through the browser cache while offline", async () => {
    const fixture = await exportFixture();
    const cached = new Map<string, Uint8Array>();
    let offline = false;
    const fetch: typeof globalThis.fetch = async (input, init) => {
      const url = input instanceof Request ? input.url : input.toString();
      if (offline) {
        const bytes = cached.get(url);
        if (bytes === undefined) throw new TypeError("network is offline");
        return new Response(bytes.slice());
      }
      const response = await fixture.fetch(input, init);
      if (init?.cache === "force-cache" && response.ok) {
        cached.set(url, new Uint8Array(await response.clone().arrayBuffer()));
      }
      return response;
    };
    const notebookExport = await openExport("https://example.test/stocks", { fetch });
    const loader = defineOutputLoader({
      codec: "numpy.npy.v1",
      accepts: () => true,
      load: ({ payload }) => payload,
    });

    await notebookExport.state("alpha").output("array").load(loader);
    offline = true;

    await expect(notebookExport.state("zeta").output("array").load(loader)).resolves.toEqual(
      new Uint8Array([0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59, 0x01, 0x00]),
    );
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
      if (new URL(url).pathname.endsWith(".npy")) {
        const bytes = new Uint8Array(await response.arrayBuffer());
        bytes[bytes.length - 1] = bytes[bytes.length - 1]! ^ 1;
        return new Response(bytes);
      }
      return response;
    };
    const notebookExport = await openExport("https://example.test/stocks?file=notebook.py", {
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
    expect(
      fixture.requests.every((request) => new URL(request).search === "?file=notebook.py"),
    ).toBe(true);
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
    const states = mutableObject(wrong.states, "states");
    const aliases = mutableObject(wrong.aliases, "aliases");
    const alpha = stringValue(aliases.alpha, "aliases.alpha");
    const wrongFingerprint = "f".repeat(64);
    states[wrongFingerprint] = states[alpha]!;
    delete states[alpha];
    if (wrong.default_state === alpha) wrong.default_state = wrongFingerprint;
    for (const alias of Object.keys(aliases)) {
      if (aliases[alias] === alpha) aliases[alias] = wrongFingerprint;
    }
    const bytes = encoder.encode(canonicalJson(wrong));
    await expect(
      openExport("https://example.test/stocks", {
        fetch: async () => new Response(bytes),
      }),
    ).rejects.toMatchObject({ code: "export_invalid" });
  });

  test("rejects a representation that changes across states", async () => {
    const fixture = await exportFixture({
      indexTransform(index) {
        const states = mutableObject(index.states, "states");
        const aliases = mutableObject(index.aliases, "aliases");
        const zetaState = mutableObject(
          states[stringValue(aliases.zeta, "aliases.zeta")],
          "states.zeta",
        );
        const outputs = mutableObject(zetaState.outputs, "states.zeta.outputs");
        const view = mutableObject(outputs.view, "states.zeta.outputs.view");
        view.media_type = "application/vnd.example.other+json";
      },
    });

    await expect(
      openExport("https://example.test/stocks", { fetch: fixture.fetch }),
    ).rejects.toMatchObject({ code: "output_representation_changed" });
  });

  test("rejects a control binding to an undeclared input", async () => {
    const fixture = await exportFixture({
      indexTransform(index) {
        index.control_bindings = {
          "cell-missing-0": { input: "missing", path: [] },
        };
      },
    });

    await expect(
      openExport("https://example.test/stocks", { fetch: fixture.fetch }),
    ).rejects.toMatchObject({ code: "export_invalid" });
  });

  test("bounds opaque input and control-binding names", async () => {
    const longName = "x".repeat(256);
    const inputFixture = await exportFixture({
      indexTransform(index) {
        index.inputs = [longName, "width"];
      },
    });
    const bindingFixture = await exportFixture({
      indexTransform(index) {
        index.control_bindings = {
          "cell-symbol-0": { input: longName, path: [] },
        };
      },
    });

    await expect(
      openExport("https://example.test/stocks", { fetch: inputFixture.fetch }),
    ).rejects.toMatchObject({ code: "export_invalid" });
    await expect(
      openExport("https://example.test/stocks", { fetch: bindingFixture.fetch }),
    ).rejects.toMatchObject({ code: "export_invalid" });
  });

  test("requires control_bindings in export v1", async () => {
    const fixture = await exportFixture({
      indexTransform(index) {
        delete index.control_bindings;
      },
    });

    await expect(
      openExport("https://example.test/stocks", { fetch: fixture.fetch }),
    ).rejects.toMatchObject({ code: "export_invalid" });
  });

  test("requires the export specification identity and declared default state", async () => {
    for (const field of ["spec_sha256", "default_state"] as const) {
      // Validation cases intentionally execute in order.
      // oxlint-disable-next-line no-await-in-loop
      const fixture = await exportFixture({
        indexTransform(index) {
          delete index[field];
        },
      });

      // oxlint-disable-next-line no-await-in-loop
      await expect(
        openExport("https://example.test/stocks", { fetch: fixture.fetch }),
      ).rejects.toMatchObject({ code: "export_invalid" });
    }

    const unknownDefault = await exportFixture({
      indexTransform(index) {
        index.default_state = "f".repeat(64);
      },
    });
    await expect(
      openExport("https://example.test/stocks", { fetch: unknownDefault.fetch }),
    ).rejects.toMatchObject({ code: "export_invalid" });
  });

  test("parses strict typed control binding paths", async () => {
    const fixture = await exportFixture({
      indexTransform(index) {
        index.control_bindings = {
          "cell-symbol-0": {
            input: "symbol",
            path: [
              { kind: "index", value: 0 },
              { kind: "key", value: "country" },
              { kind: "element" },
            ],
          },
        };
      },
    });
    const notebookExport = await openExport("https://example.test/stocks", {
      fetch: fixture.fetch,
    });

    expect(notebookExport.controlBindings["cell-symbol-0"]!.path).toEqual([
      { kind: "index", value: 0 },
      { kind: "key", value: "country" },
      { kind: "element" },
    ]);
  });

  test("rejects malformed control binding path steps", async () => {
    for (const step of [
      { kind: "element", value: 0 },
      { kind: "index", value: -1 },
      { kind: "key", value: "country", extra: true },
    ]) {
      // Validation cases intentionally execute in order.
      // oxlint-disable-next-line no-await-in-loop
      const fixture = await exportFixture({
        indexTransform(index) {
          index.control_bindings = {
            "cell-symbol-0": { input: "symbol", path: [step] },
          };
        },
      });
      // oxlint-disable-next-line no-await-in-loop
      await expect(
        openExport("https://example.test/stocks", { fetch: fixture.fetch }),
      ).rejects.toMatchObject({ code: "export_invalid" });
    }
  });

  test("requires the producer implementation identity", async () => {
    const fixture = await exportFixture({
      indexTransform(index) {
        const producer = mutableObject(index.producer, "producer");
        delete producer.implementation_sha256;
      },
    });

    await expect(
      openExport("https://example.test/stocks", { fetch: fixture.fetch }),
    ).rejects.toMatchObject({ code: "export_invalid" });
  });

  test.each([
    ["cache_key", "cell_cache/O_count.json"],
    ["return_reference", null],
  ])("rejects private cache receipt field %s in provenance", async (field, value) => {
    const fixture = await exportFixture({
      indexTransform(index) {
        const states = mutableObject(index.states, "states");
        const aliases = mutableObject(index.aliases, "aliases");
        const state = mutableObject(
          states[stringValue(aliases.alpha, "aliases.alpha")],
          "states.alpha",
        );
        const outputs = mutableObject(state.outputs, "states.alpha.outputs");
        const count = mutableObject(outputs.count, "states.alpha.outputs.count");
        const provenance = mutableObject(count.provenance, "states.alpha.outputs.count.provenance");
        provenance[field] = value;
      },
    });

    await expect(
      openExport("https://example.test/stocks", { fetch: fixture.fetch }),
    ).rejects.toMatchObject({ code: "export_invalid" });
  });
});
