import { describe, expect, test, vi } from "vite-plus/test";

import { openExport, snapshotExport } from "../src/reader.js";
import { parseExportManifest } from "../src/schema.js";
import { memorySource } from "../src/source.js";
import type { ExportSource } from "../src/types.js";
import { MarimoExportError } from "../src/types.js";
import { exportFixture } from "./fixture.js";

describe("published notebook reader", () => {
  test("selects scenarios by id and exact input vector", async () => {
    const fixture = await exportFixture();
    const published = await openExport(memorySource(fixture.objects));

    expect(published.ref).toEqual(fixture.ref);
    expect(published.notebook).toEqual({
      name: "finance.py",
      sourceSha256: "b".repeat(64),
    });
    expect(published.planSha256).toBe("c".repeat(64));
    expect(published.producer).toEqual({
      marimoVersion: "0.23.14",
      marimoExportVersion: "0.0.0",
    });
    expect(published.scenario("microsoft").id).toBe("microsoft");
    expect(published.resolve({ window: 30, symbol: "AAPL" }).id).toBe("apple");
    expect(published.scenarios().map((scenario) => scenario.id)).toEqual(["microsoft", "apple"]);
  });

  test("infers a single format and requires a choice for multiple formats", async () => {
    const fixture = await exportFixture();
    const scenario = (await openExport(memorySource(fixture.objects))).scenario("microsoft");

    expect(scenario.output("empty").formatName).toBe("text");
    expect(() => scenario.output("prices")).toThrow(
      expect.objectContaining({
        code: "ambiguous_format",
        message: expect.stringMatching(/multiple formats/),
        details: {
          scenario: "microsoft",
          output: "prices",
          available: ["json", "text"],
        },
      }),
    );
    expect(() => scenario.output("missing")).toThrow(
      expect.objectContaining({
        code: "missing_output",
        message: expect.stringMatching(/Available outputs/),
        details: {
          scenario: "microsoft",
          output: "missing",
          available: ["prices", "empty"],
        },
      }),
    );
    expect(() => scenario.output("prices", "missing")).toThrow(
      expect.objectContaining({
        code: "missing_format",
        message: expect.stringMatching(/Available formats/),
        details: {
          scenario: "microsoft",
          output: "prices",
          format: "missing",
          available: ["json", "text"],
        },
      }),
    );
    expect(scenario.outputs().map((output) => `${output.name}/${output.formatName}`)).toEqual([
      "prices/json",
      "prices/text",
      "empty/text",
    ]);
  });

  test("reads verified bytes, text, JSON, blobs, and loaders", async () => {
    const fixture = await exportFixture();
    const output = (await openExport(memorySource(fixture.objects)))
      .scenario("microsoft")
      .output("prices", "json");

    expect(await output.json()).toEqual([{ symbol: "MSFT", price: 420 }]);
    expect(
      await output.json((value) => {
        const rows = value as Array<{ price: number }>;
        return rows[0]!.price;
      }),
    ).toBe(420);
    expect(await output.text()).toBe('[{"symbol":"MSFT","price":420}]');
    expect((await output.blob()).type).toBe("application/json");
    expect(
      await output.load({
        formatId: "json.v1",
        async load(context) {
          return {
            formatId: context.formatId,
            metadata: context.metadata,
            size: context.size,
            value: await context.json(),
          };
        },
      }),
    ).toMatchObject({
      formatId: "json.v1",
      metadata: { rows: 1 },
      value: [{ symbol: "MSFT", price: 420 }],
    });
    await expect(output.load({ formatId: "text.v1", load: () => "wrong" })).rejects.toMatchObject({
      code: "unsupported_format",
    });
    await expect(
      output.load(
        {
          formatId: "json.v1",
          // Extra arguments are ignored at runtime so loader code cannot replace
          // the read policy bound by ExportOutput.load().
          load: (context) =>
            (context.bytes as (options?: { maxBytes?: number }) => Promise<Uint8Array>)({
              maxBytes: 10_000,
            }),
        },
        { maxBytes: 1 },
      ),
    ).rejects.toMatchObject({ code: "output_too_large" });
  });

  test("supports semantic zero-byte payloads", async () => {
    const fixture = await exportFixture();
    const output = (await openExport(memorySource(fixture.objects)))
      .scenario("microsoft")
      .output("empty");

    expect(await output.bytes()).toHaveLength(0);
    expect(await output.text()).toBe("");
  });

  test("deduplicates in-flight reads, evicts fulfilled bytes, and returns defensive copies", async () => {
    const fixture = await exportFixture();
    const source = memorySource(fixture.objects);
    const read = vi.fn(source.read.bind(source));
    const published = await openExport({ read });
    const microsoft = published.scenario("microsoft").output("prices", "json");
    const apple = published.scenario("apple").output("prices", "json");

    const [first, shared] = await Promise.all([microsoft.bytes(), apple.bytes()]);
    expect(first).toEqual(shared);
    expect(read.mock.calls.filter(([path]) => path !== "index.json")).toHaveLength(1);
    first[0] = 0;
    expect(shared).toEqual(fixture.jsonPayload);

    expect(await apple.bytes()).toEqual(fixture.jsonPayload);
    expect(read.mock.calls.filter(([path]) => path !== "index.json")).toHaveLength(2);
  });

  test("owns custom source bytes before asynchronous integrity checks", async () => {
    const fixture = await exportFixture();
    const indexBytes = new Uint8Array(fixture.indexBytes);
    const payloadBytes = new Uint8Array(fixture.jsonPayload);
    const payloadPath = `cache/${fixture.index.scenarios[0]!.outputs.prices!.json!.payload.key}`;
    const originalDigest = globalThis.crypto.subtle.digest.bind(globalThis.crypto.subtle);
    const digest = vi
      .spyOn(globalThis.crypto.subtle, "digest")
      .mockImplementation(async (algorithm, input) => {
        const view = input as Uint8Array<ArrayBuffer>;
        const snapshot = new Uint8Array(view);
        await new Promise<void>((resolve) => setTimeout(resolve, 0));
        return originalDigest(algorithm, snapshot);
      });
    const source: ExportSource = {
      async read(path) {
        const bytes = path === "index.json" ? indexBytes : payloadBytes;
        if (path !== "index.json" && path !== payloadPath) {
          throw new Error(`unexpected path: ${path}`);
        }
        setTimeout(() => bytes.fill(0), 0);
        return bytes;
      },
    };

    try {
      const output = (await openExport(source, { ref: fixture.ref }))
        .scenario("microsoft")
        .output("prices", "json");
      expect(await output.bytes()).toEqual(fixture.jsonPayload);
    } finally {
      digest.mockRestore();
    }
  });

  test("clears failed payload reads so a caller can retry", async () => {
    const fixture = await exportFixture();
    const fallback = memorySource(fixture.objects);
    let payloadAttempts = 0;
    const source: ExportSource = {
      async read(path, options) {
        if (path !== "index.json" && payloadAttempts++ === 0) {
          return new TextEncoder().encode("corrupt");
        }
        return fallback.read(path, options);
      },
    };
    const output = (await openExport(source)).scenario("microsoft").output("prices", "json");

    await expect(output.bytes()).rejects.toMatchObject({ code: "integrity_failed" });
    expect(await output.bytes()).toEqual(fixture.jsonPayload);
    expect(payloadAttempts).toBe(2);
  });

  test("rejects a same-length payload with the wrong digest", async () => {
    const fixture = await exportFixture();
    const projection = fixture.index.scenarios[0]!.outputs.prices!.json!;
    const corrupted = new Uint8Array(fixture.jsonPayload);
    corrupted[0] = corrupted[0]! ^ 1;
    const source = memorySource({
      ...fixture.objects,
      [`cache/${projection.payload.key}`]: corrupted,
    });
    const output = (await openExport(source)).scenario("microsoft").output("prices", "json");

    await expect(output.bytes()).rejects.toMatchObject({ code: "integrity_failed" });
  });

  test("forwards a caller signal to the source read", async () => {
    const fixture = await exportFixture();
    const fallback = memorySource(fixture.objects);
    let receivedSignal: AbortSignal | undefined;
    const source: ExportSource = {
      async read(path, options) {
        if (path === "index.json") return fallback.read(path, options);
        receivedSignal = options?.signal;
        return new Promise<Uint8Array>((_resolve, reject) => {
          options?.signal?.addEventListener("abort", () => reject(options.signal?.reason), {
            once: true,
          });
        });
      },
    };
    const output = (await openExport(source)).scenario("microsoft").output("prices", "json");
    const controller = new AbortController();
    const cancelled = output.bytes({ signal: controller.signal });
    controller.abort(new DOMException("cancelled", "AbortError"));

    await expect(cancelled).rejects.toMatchObject({ name: "AbortError" });
    expect(receivedSignal).toBe(controller.signal);
  });

  test("rejects cancellation that races index source completion", async () => {
    const fixture = await exportFixture();
    const controller = new AbortController();
    const reason = new DOMException("cancelled", "AbortError");
    const source: ExportSource = {
      async read() {
        controller.abort(reason);
        return fixture.indexBytes;
      },
    };

    await expect(openExport(source, { signal: controller.signal })).rejects.toBe(reason);
  });

  test("rejects cancellation that races index hashing", async () => {
    const fixture = await exportFixture();
    const controller = new AbortController();
    const reason = new DOMException("cancelled", "AbortError");
    const originalDigest = globalThis.crypto.subtle.digest.bind(globalThis.crypto.subtle);
    const digest = vi
      .spyOn(globalThis.crypto.subtle, "digest")
      .mockImplementation(async (...args) => {
        const result = await originalDigest(...args);
        controller.abort(reason);
        return result;
      });

    try {
      await expect(
        openExport(memorySource(fixture.objects), { signal: controller.signal }),
      ).rejects.toBe(reason);
    } finally {
      digest.mockRestore();
    }
  });

  test("rejects cancellation that races payload verification", async () => {
    const fixture = await exportFixture();
    const output = (await openExport(memorySource(fixture.objects)))
      .scenario("microsoft")
      .output("prices", "json");
    const controller = new AbortController();
    const reason = new DOMException("cancelled", "AbortError");
    const originalDigest = globalThis.crypto.subtle.digest.bind(globalThis.crypto.subtle);
    const digest = vi
      .spyOn(globalThis.crypto.subtle, "digest")
      .mockImplementation(async (...args) => {
        const result = await originalDigest(...args);
        controller.abort(reason);
        return result;
      });

    try {
      await expect(output.bytes({ signal: controller.signal })).rejects.toBe(reason);
    } finally {
      digest.mockRestore();
    }
  });

  test("bounds index and payload source reads by verified references", async () => {
    const fixture = await exportFixture();
    const fallback = memorySource(fixture.objects);
    const limits = new Map<string, number | undefined>();
    const source: ExportSource = {
      async read(path, options) {
        limits.set(path, options?.maxBytes);
        return fallback.read(path, options);
      },
    };
    const published = await openExport(source, { ref: fixture.ref });
    const output = published.scenario("microsoft").output("prices", "json");
    await output.bytes();

    expect(limits.get("index.json")).toBe(fixture.ref.size);
    expect(limits.get(`cache/${output.ref.key}`)).toBe(output.ref.size);
  });

  test("verifies an external ExportRef before parsing the index", async () => {
    const fixture = await exportFixture();
    const published = await openExport(memorySource(fixture.objects), { ref: fixture.ref });
    expect(published.scenario("apple").id).toBe("apple");

    const invalid = memorySource({ "index.json": "not JSON" });
    await expect(openExport(invalid, { ref: fixture.ref })).rejects.toMatchObject({
      code: "integrity_failed",
    });
    await expect(
      openExport(memorySource(fixture.objects), {
        ref: { ...fixture.ref, key: "marimo-export/indexes/wrong.json" },
      }),
    ).rejects.toMatchObject({ code: "invalid_ref" });
  });

  test("keeps the manifest, source, and index bytes behind the domain API", async () => {
    const fixture = await exportFixture();
    const published = await openExport(memorySource(fixture.objects));
    const scenario = published.scenario("microsoft");
    const output = scenario.output("prices", "json");

    expect(Object.isFrozen(published)).toBe(true);
    expect(Object.isFrozen(published.notebook)).toBe(true);
    expect(Object.isFrozen(published.scenarios())).toBe(true);
    expect(Object.isFrozen(scenario)).toBe(true);
    expect(Object.isFrozen(scenario.inputs)).toBe(true);
    expect(Object.isFrozen(scenario.outputs())).toBe(true);
    expect(Object.isFrozen(output)).toBe(true);
    expect(Object.isFrozen(output.metadata)).toBe(true);
    expect(Object.isFrozen(output.ref)).toBe(true);
    expect("source" in published).toBe(false);
    expect("manifest" in published).toBe(false);
    expect("indexBytes" in published).toBe(false);

    const snapshot = snapshotExport(published);
    snapshot.indexBytes[0] = 0;
    expect(snapshotExport(published).indexBytes).toEqual(fixture.indexBytes);
    expect(snapshot.payloads).toHaveLength(3);
  });

  test("strictly validates the v1 projection-only manifest", async () => {
    const fixture = await exportFixture();
    expect(() => parseExportManifest({ ...fixture.index, files: [] })).toThrow(/unexpected fields/);
    expect(() =>
      parseExportManifest({
        ...fixture.index,
        scenarios: fixture.index.scenarios.map((scenario, index) =>
          index === 0
            ? { ...scenario, inputs: { ...scenario.inputs, unsafe: 9_007_199_254_740_992 } }
            : scenario,
        ),
      }),
    ).toThrow(/safe JSON integer/);
    expect(() =>
      parseExportManifest({
        ...fixture.index,
        scenarios: [
          fixture.index.scenarios[0]!,
          {
            ...fixture.index.scenarios[1]!,
            inputs: { window: 30, symbol: "MSFT" },
          },
        ],
      }),
    ).toThrow(/duplicate scenario input vector/);
    const first = fixture.index.scenarios[0]!;
    const json = first.outputs.prices!.json!;
    expect(() =>
      parseExportManifest({
        ...fixture.index,
        scenarios: [
          {
            ...first,
            outputs: {
              ...first.outputs,
              prices: {
                ...first.outputs.prices,
                json: {
                  ...json,
                  payload: { ...json.payload, key: "somewhere/else" },
                },
              },
            },
          },
        ],
      }),
    ).toThrow(/payload\.key must be/);
  });

  test("rejects invalid JSON payloads after integrity verification", async () => {
    const fixture = await exportFixture();
    const index: unknown = structuredClone(fixture.index);
    const mutable = index as {
      scenarios: Array<{
        outputs: { prices: { json: { payload: { key: string; sha256: string; size: number } } } };
      }>;
    };
    const bytes = new TextEncoder().encode("not-json");
    const { sha256Hex } = await import("../src/hash.js");
    const digest = await sha256Hex(bytes);
    const projection = mutable.scenarios[0]!.outputs.prices.json;
    projection.payload = {
      key: `marimo-export/payloads/sha256/${digest}`,
      sha256: digest,
      size: bytes.byteLength,
    };
    const indexBytes = new TextEncoder().encode(JSON.stringify(index));
    const source = memorySource({
      "index.json": indexBytes,
      [`cache/${projection.payload.key}`]: bytes,
    });
    const output = (await openExport(source)).scenario("microsoft").output("prices", "json");

    await expect(output.json()).rejects.toBeInstanceOf(MarimoExportError);
    await expect(output.json()).rejects.toMatchObject({ code: "decode_failed" });
  });

  test("preserves custom source failures through text and JSON reads", async () => {
    const fixture = await exportFixture();
    const failure = new Error("storage unavailable");
    const fallback = memorySource(fixture.objects);
    const source: ExportSource = {
      async read(path, options) {
        if (path !== "index.json") throw failure;
        return fallback.read(path, options);
      },
    };
    const output = (await openExport(source)).scenario("microsoft").output("prices", "json");

    await expect(output.text()).rejects.toBe(failure);
    await expect(output.json()).rejects.toBe(failure);
  });

  test("snapshots loader read options before asynchronous loader work", async () => {
    const fixture = await exportFixture();
    const output = (await openExport(memorySource(fixture.objects)))
      .scenario("microsoft")
      .output("prices", "json");
    const options = { maxBytes: 1 };
    const loading = output.load(
      {
        formatId: "json.v1",
        async load(context) {
          await Promise.resolve();
          return context.bytes();
        },
      },
      options,
    );
    options.maxBytes = 10_000;

    await expect(loading).rejects.toMatchObject({ code: "output_too_large" });
  });

  test("exposes the load signal to custom decoding work", async () => {
    const fixture = await exportFixture();
    const output = (await openExport(memorySource(fixture.objects)))
      .scenario("microsoft")
      .output("prices", "json");
    const controller = new AbortController();
    const reason = new DOMException("decode cancelled", "AbortError");
    let observed: AbortSignal | undefined;
    const loading = output.load(
      {
        formatId: "json.v1",
        load(context) {
          observed = context.signal;
          return new Promise<never>((_resolve, reject) => {
            context.signal?.addEventListener("abort", () => reject(context.signal?.reason), {
              once: true,
            });
          });
        },
      },
      { signal: controller.signal },
    );

    controller.abort(reason);

    await expect(loading).rejects.toBe(reason);
    expect(observed).toBe(controller.signal);
  });
});
