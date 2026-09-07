import { describe, expect, it, vi } from "vite-plus/test";

import { fetchPreparedManifestDocument } from "../src/prepared/index.js";

describe("prepared manifest documents", () => {
  it("rejects a declared oversized manifest before reading its body", async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(new TextEncoder().encode("{}"));
        controller.close();
      },
      cancel() {
        cancelled = true;
      },
    });
    const fetcher = vi.fn(
      async () =>
        new Response(body, {
          headers: { "Content-Length": String(256 * 1024 + 1) },
        }),
    );

    await expect(
      fetchPreparedManifestDocument(new URL("https://example.test/current"), { fetch: fetcher }),
    ).rejects.toThrow(/exceeds 262144 bytes/);
    expect(cancelled).toBe(true);
  });

  it("stops streaming a manifest when its body crosses the byte limit", async () => {
    let pulls = 0;
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>(
      {
        pull(controller) {
          pulls += 1;
          if (pulls > 4) {
            controller.close();
            return;
          }
          controller.enqueue(new Uint8Array(128 * 1024));
        },
        cancel() {
          cancelled = true;
        },
      },
      { highWaterMark: 0 },
    );
    const fetcher = vi.fn(async () => new Response(body));

    await expect(
      fetchPreparedManifestDocument(new URL("https://example.test/current"), { fetch: fetcher }),
    ).rejects.toThrow(/exceeds 262144 bytes/);
    expect(cancelled).toBe(true);
    expect(pulls).toBe(3);
  });

  it("cancels a response body when the caller aborts as fetch completes", async () => {
    const controller = new AbortController();
    const reason = new DOMException("manifest superseded", "AbortError");
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      pull(stream) {
        stream.enqueue(new TextEncoder().encode("{}"));
      },
      cancel() {
        cancelled = true;
      },
    });
    const fetcher = vi.fn(async () => {
      controller.abort(reason);
      return new Response(body);
    });

    await expect(
      fetchPreparedManifestDocument(new URL("https://example.test/current"), {
        fetch: fetcher,
        signal: controller.signal,
      }),
    ).rejects.toBe(reason);
    expect(cancelled).toBe(true);
  });

  it("reads application envelopes through the bounded portable JSON contract", async () => {
    const fetcher = vi.fn(
      async () => new Response('{"schema":"report.v1","prepared":{"inputs":{}}}'),
    );
    const url = new URL("https://example.test/current");
    await expect(fetchPreparedManifestDocument(url, { fetch: fetcher })).resolves.toEqual({
      schema: "report.v1",
      prepared: { inputs: {} },
    });
    expect(fetcher).toHaveBeenCalledWith(url, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
  });

  it.each([
    new Uint8Array([0xff]),
    new TextEncoder().encode('{"value":'),
    new TextEncoder().encode('{"value":1,"value":2}'),
  ])("rejects malformed UTF-8 and portable JSON", async (bytes) => {
    await expect(
      fetchPreparedManifestDocument(new URL("https://example.test/current"), {
        fetch: async () => new Response(bytes),
      }),
    ).rejects.toMatchObject({ code: "manifest_invalid" });
  });

  it("cancels a body while its read is pending", async () => {
    const controller = new AbortController();
    let started: (() => void) | undefined;
    const reading = new Promise<void>((resolve) => {
      started = resolve;
    });
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>(
      {
        pull() {
          started?.();
        },
        cancel() {
          cancelled = true;
        },
      },
      { highWaterMark: 0 },
    );
    const operation = fetchPreparedManifestDocument(new URL("https://example.test/current"), {
      fetch: async () => new Response(body),
      signal: controller.signal,
    });
    await reading;
    controller.abort(new DOMException("view replaced", "AbortError"));
    await expect(operation).rejects.toMatchObject({ name: "AbortError" });
    expect(cancelled).toBe(true);
  });
});
