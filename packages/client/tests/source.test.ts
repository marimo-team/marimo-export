import { afterEach, describe, expect, test, vi } from "vite-plus/test";

import { httpSource, memorySource } from "../src/source.js";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HTTP export source", () => {
  test("resolves portable paths below one materialized root", async () => {
    const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : input instanceof URL ? input.href : input;
      expect(url).toBe("https://example.test/public/export/cache/payload%20name");
      expect(init?.redirect).toBe("error");
      return new Response(new Uint8Array([1, 2, 3]));
    });
    const source = httpSource("https://example.test/public/export", { fetch });

    expect(await source.read("cache/payload name")).toEqual(new Uint8Array([1, 2, 3]));
    expect(fetch).toHaveBeenCalledOnce();
  });

  test("resolves a relative root against an explicit SSR base", async () => {
    const fetch = vi.fn(async (input: string | URL | Request) => {
      const url = input instanceof Request ? input.url : input instanceof URL ? input.href : input;
      expect(url).toBe("https://example.test/site/finance/index.json");
      return new Response(new Uint8Array([1]));
    });
    const source = httpSource("./finance", {
      base: "https://example.test/site/",
      fetch,
    });

    await source.read("index.json");
  });

  test("resolves a site-relative root against browser location", async () => {
    vi.stubGlobal("location", { href: "https://example.test/app/page/?view=compact#results" });
    const fetch = vi.fn(async (input: string | URL | Request) => {
      const url = input instanceof Request ? input.url : input instanceof URL ? input.href : input;
      expect(url).toBe("https://example.test/exports/finance/index.json");
      return new Response(new Uint8Array([1]));
    });
    const source = httpSource("/exports/finance", { fetch });

    await source.read("index.json");
  });

  test("snapshots headers at construction", async () => {
    const headers = { authorization: "Bearer first" };
    const fetch = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      expect(init?.headers).toEqual({ authorization: "Bearer first" });
      return new Response(new Uint8Array());
    });
    const source = httpSource("https://example.test/export", { fetch, headers });
    headers.authorization = "Bearer changed";

    await source.read("index.json");
  });

  test("rejects invalid roots before reading", () => {
    vi.stubGlobal("location", undefined);
    expect(() => httpSource("./finance")).toThrow(
      "Relative HTTP export roots require options.base outside a browser.",
    );
    expect(() => httpSource("file:///tmp/export")).toThrow(/HTTP or HTTPS/);
    expect(() => httpSource("https://user:secret@example.test/export")).toThrow(
      /embedded credentials/,
    );
    expect(() => httpSource("https://example.test/export?token=secret")).toThrow(
      /query or fragment/,
    );
    expect(() =>
      httpSource("./finance", { base: "https://user:secret@example.test/site/" }),
    ).toThrow(/embedded credentials/);
    expect(() =>
      httpSource("./finance?token=secret", { base: "https://example.test/site/" }),
    ).toThrow(/query or fragment/);
  });

  test("allows route query and fragment state in an SSR resolution base", async () => {
    const fetch = vi.fn(async (input: string | URL | Request) => {
      const url = input instanceof Request ? input.url : input instanceof URL ? input.href : input;
      expect(url).toBe("https://example.test/app/finance/index.json");
      return new Response(new Uint8Array([1]));
    });
    const source = httpSource("./finance", {
      base: "https://example.test/app/page?view=compact#results",
      fetch,
    });

    await source.read("index.json");
  });

  test("enforces actual streamed bytes when content length is missing or wrong", async () => {
    const encoder = new TextEncoder();
    await Promise.all(
      [undefined, "1"].map(async (contentLength) => {
        const fetch = vi.fn(async () => {
          const body = new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(encoder.encode("ab"));
              controller.enqueue(encoder.encode("cd"));
              controller.close();
            },
          });
          return new Response(body, {
            headers: contentLength === undefined ? {} : { "Content-Length": contentLength },
          });
        });
        const source = httpSource("https://example.test/export", { fetch });

        await expect(source.read("cache/value", { maxBytes: 3 })).rejects.toMatchObject({
          code: "output_too_large",
        });
      }),
    );
  });

  test("rejects redirected custom fetch responses", async () => {
    const response = new Response(new Uint8Array([1]));
    Object.defineProperty(response, "redirected", { value: true });
    const source = httpSource("https://example.test/export", {
      fetch: async () => response,
      headers: { Authorization: "Bearer secret" },
    });

    await expect(source.read("index.json", { maxBytes: 1 })).rejects.toMatchObject({
      code: "source_read_failed",
    });
  });

  test("does not wait for a custom response body cancellation on HTTP rejection", async () => {
    let cancellations = 0;
    const body = new ReadableStream<Uint8Array>({
      cancel() {
        cancellations += 1;
        return new Promise<void>(() => undefined);
      },
    });
    const source = httpSource("https://example.test/export", {
      fetch: async () => new Response(body, { status: 500 }),
    });

    await expect(source.read("index.json", { maxBytes: 1 })).rejects.toMatchObject({
      code: "source_read_failed",
    });
    expect(cancellations).toBe(1);
  });

  test("does not wait for stream cancellation after exceeding the byte limit", async () => {
    let cancellations = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(new Uint8Array([1, 2]));
      },
      cancel() {
        cancellations += 1;
        return new Promise<void>(() => undefined);
      },
    });
    const source = httpSource("https://example.test/export", {
      fetch: async () => new Response(body),
    });

    await expect(source.read("index.json", { maxBytes: 1 })).rejects.toMatchObject({
      code: "output_too_large",
    });
    expect(cancellations).toBe(1);
  });
});

describe("memory export source", () => {
  test("copies input and output byte arrays", async () => {
    const bytes = new Uint8Array([1, 2, 3]);
    const source = memorySource({ "cache/value": bytes });
    bytes[0] = 9;
    const first = await source.read("cache/value");
    first[1] = 9;

    expect(await source.read("cache/value")).toEqual(new Uint8Array([1, 2, 3]));
  });

  test("accepts the declared ReadonlyMap contract", async () => {
    const values = new Map<string, Uint8Array | string>([["index.json", "{}"]]);
    const view: ReadonlyMap<string, Uint8Array | string> = {
      get size() {
        return values.size;
      },
      entries: () => values.entries(),
      forEach: (callback, thisArg) => values.forEach(callback, thisArg),
      get: (key) => values.get(key),
      has: (key) => values.has(key),
      keys: () => values.keys(),
      values: () => values.values(),
      [Symbol.iterator]: () => values[Symbol.iterator](),
    };

    await expect(memorySource(view).read("index.json")).resolves.toEqual(
      new TextEncoder().encode("{}"),
    );
  });

  test("rejects traversal paths", async () => {
    expect(() => memorySource({ "../index.json": "bad" })).toThrow(/portable relative path/);
    const source = memorySource({ "index.json": "{}" });
    await expect(source.read("../index.json")).rejects.toMatchObject({
      code: "source_read_failed",
    });
  });

  test("enforces read limits before copying stored bytes", async () => {
    const source = memorySource({ "cache/value": new Uint8Array([1, 2]) });
    await expect(source.read("cache/value", { maxBytes: 1 })).rejects.toMatchObject({
      code: "output_too_large",
    });
  });
});
