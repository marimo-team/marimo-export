import { afterEach, describe, expect, test, vi } from "vite-plus/test";

import { httpSource, memorySource } from "../src/source.js";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HTTP publication source", () => {
  test("resolves and encodes portable paths below the publication root", async () => {
    const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : input instanceof URL ? input.href : input;
      expect(url).toBe("https://example.test/public/export/cache/C%20value/return.bin");
      expect(init?.redirect).toBe("error");
      return new Response(new Uint8Array([1, 2, 3]));
    });
    const source = httpSource("https://example.test/public/export", { fetch });

    expect(await source.read("cache/C value/return.bin")).toEqual(new Uint8Array([1, 2, 3]));
    expect(fetch).toHaveBeenCalledOnce();
  });

  test("resolves relative roots against the browser location", async () => {
    vi.stubGlobal("location", { href: "https://example.test/app/page/?view=compact#results" });
    const fetch = vi.fn(async (input: string | URL | Request) => {
      const url = input instanceof Request ? input.url : input instanceof URL ? input.href : input;
      expect(url).toBe("https://example.test/exports/finance/index.json");
      return new Response(new Uint8Array([1]));
    });
    const source = httpSource("/exports/finance", { fetch });

    await source.read("index.json");
  });

  test("snapshots request headers", async () => {
    const headers = { authorization: "Bearer first" };
    const fetch = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      expect(init?.headers).toEqual({ authorization: "Bearer first" });
      return new Response(new Uint8Array());
    });
    const source = httpSource("https://example.test/export", { fetch, headers });
    headers.authorization = "Bearer changed";

    await source.read("index.json");
  });

  test("validates headers before a request", () => {
    const fetch = vi.fn<typeof globalThis.fetch>();

    expect(() =>
      httpSource("https://example.test/export", {
        fetch,
        headers: { "bad\nname": "value" },
      }),
    ).toThrow(TypeError);
    expect(fetch).not.toHaveBeenCalled();
  });

  test("rejects roots that cannot identify one HTTP directory", () => {
    vi.stubGlobal("location", undefined);
    expect(() => httpSource("./finance")).toThrow("require a browser location");
    expect(() => httpSource("file:///tmp/export")).toThrow(/HTTP or HTTPS/);
    expect(() => httpSource("https://user:secret@example.test/export")).toThrow(
      /embedded credentials/,
    );
    expect(() => httpSource("https://example.test/export?token=secret")).toThrow(
      /query or fragment/,
    );
    expect(() => httpSource("https://example.test/export?")).toThrow(/query or fragment/);
    expect(() => httpSource("https://example.test/export#")).toThrow(/query or fragment/);
    expect(() => httpSource("https://example.test/export/?")).toThrow(/query or fragment/);
    expect(() => httpSource("https://example.test/export/#")).toThrow(/query or fragment/);
    expect(() => httpSource(new URL("https://example.test/export?"))).toThrow(/query or fragment/);
    expect(() => httpSource(new URL("https://example.test/export#"))).toThrow(/query or fragment/);
  });

  test("enforces streamed bytes when content length is missing or incorrect", async () => {
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
          code: "read_limit_exceeded",
        });
      }),
    );
  });

  test("coalesces many one-byte chunks", async () => {
    const expectedSize = 64 * 1024;
    let emitted = 0;
    const source = httpSource("https://example.test/export", {
      fetch: async () =>
        new Response(
          new ReadableStream<Uint8Array>({
            pull(controller) {
              if (emitted === expectedSize) {
                controller.close();
                return;
              }
              controller.enqueue(Uint8Array.of(emitted % 251));
              emitted += 1;
            },
          }),
        ),
    });

    const bytes = await source.read("index.json", { maxBytes: expectedSize });

    expect(bytes).toHaveLength(expectedSize);
    expect(bytes[0]).toBe(0);
    expect(bytes.at(-1)).toBe((expectedSize - 1) % 251);
  });

  test("skips repeated empty response chunks", async () => {
    let emitted = 0;
    const source = httpSource("https://example.test/export", {
      fetch: async () =>
        new Response(
          new ReadableStream<Uint8Array>({
            pull(controller) {
              if (emitted < 4_096) {
                emitted += 1;
                controller.enqueue(new Uint8Array());
                return;
              }
              if (emitted === 4_096) {
                emitted += 1;
                controller.enqueue(Uint8Array.of(1, 2));
                return;
              }
              controller.close();
            },
          }),
        ),
    });

    await expect(source.read("index.json", { maxBytes: 2 })).resolves.toEqual(Uint8Array.of(1, 2));
  });

  test("bounds decoded bytes instead of compressed Content-Length", async () => {
    const source = httpSource("https://example.test/export", {
      fetch: async () =>
        new Response(Uint8Array.of(1), {
          headers: {
            "Content-Encoding": "gzip",
            "Content-Length": "99",
          },
        }),
    });

    await expect(source.read("index.json", { maxBytes: 1 })).resolves.toEqual(Uint8Array.of(1));
  });

  test("rejects content lengths outside the safe integer range", async () => {
    const source = httpSource("https://example.test/export", {
      fetch: async () =>
        new Response(new Uint8Array(), {
          headers: { "Content-Length": "999999999999999999999999" },
        }),
    });

    await expect(source.read("index.json", { maxBytes: 1 })).rejects.toMatchObject({
      code: "read_limit_exceeded",
      details: { declaredBytes: "999999999999999999999999" },
    });
  });

  test("rejects redirects and HTTP failures", async () => {
    const redirected = new Response(new Uint8Array([1]));
    Object.defineProperty(redirected, "redirected", { value: true });
    const redirectSource = httpSource("https://example.test/export", {
      fetch: async () => redirected,
    });
    await expect(redirectSource.read("index.json", { maxBytes: 1 })).rejects.toMatchObject({
      code: "read_failed",
    });

    const failedSource = httpSource("https://example.test/export", {
      fetch: async () => new Response(null, { status: 503, statusText: "Unavailable" }),
    });
    await expect(failedSource.read("index.json")).rejects.toMatchObject({
      code: "read_failed",
    });
  });

  test("cancels a custom response returned after the read was aborted", async () => {
    let cancellations = 0;
    const controller = new AbortController();
    const body = new ReadableStream<Uint8Array>({
      cancel() {
        cancellations += 1;
      },
    });
    const source = httpSource("https://example.test/export", {
      fetch: async () => {
        controller.abort();
        return new Response(body);
      },
    });

    await expect(source.read("index.json", { signal: controller.signal })).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(cancellations).toBe(1);
  });

  test("settles an abort while a custom response stream read is pending", async () => {
    let markStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    let cancellations = 0;
    const body = new ReadableStream<Uint8Array>({
      pull() {
        markStarted();
        return new Promise<void>(() => undefined);
      },
      cancel() {
        cancellations += 1;
      },
    });
    const source = httpSource("https://example.test/export", {
      fetch: async () => new Response(body),
    });
    const controller = new AbortController();
    const reading = source.read("index.json", { signal: controller.signal });
    await started;

    controller.abort();

    await expect(reading).rejects.toMatchObject({ name: "AbortError" });
    expect(cancellations).toBe(1);
  });

  test("cancels an over-limit stream", async () => {
    let cancellations = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(new Uint8Array([1, 2]));
      },
      cancel() {
        cancellations += 1;
      },
    });
    const source = httpSource("https://example.test/export", {
      fetch: async () => new Response(body),
    });

    await expect(source.read("index.json", { maxBytes: 1 })).rejects.toMatchObject({
      code: "read_limit_exceeded",
    });
    expect(cancellations).toBe(1);
  });
});

describe("memory publication source", () => {
  test("copies input and output byte arrays", async () => {
    const bytes = new Uint8Array([1, 2, 3]);
    const source = memorySource({ "cache/value": bytes });
    bytes[0] = 9;
    const first = await source.read("cache/value");
    first[1] = 9;

    expect(await source.read("cache/value")).toEqual(new Uint8Array([1, 2, 3]));
  });

  test("rejects traversal paths and enforces read limits", async () => {
    expect(() => memorySource({ "../index.json": "bad" })).toThrow(/portable relative POSIX/);
    expect(() => memorySource({ "cache/value.bin:secret": "bad" })).toThrow(
      /portable relative POSIX/,
    );
    expect(() => memorySource({ "cache/CON.bin": "bad" })).toThrow(/portable relative POSIX/);
    const source = memorySource({ "index.json": "{}", "cache/value": new Uint8Array([1, 2]) });
    await expect(source.read("../index.json")).rejects.toMatchObject({ code: "read_failed" });
    await expect(source.read("cache/value", { maxBytes: 1 })).rejects.toMatchObject({
      code: "read_limit_exceeded",
    });
  });
});
