import { encode } from "@msgpack/msgpack";
import { openPublication } from "@marimo-team/marimo-export";
import type { PublishedFormat } from "@marimo-team/marimo-export";
import { beforeEach, describe, expect, test, vi } from "vite-plus/test";

import { vegaLiteLoader } from "../src/index.js";

const { embed, finalize } = vi.hoisted(() => ({
  embed: vi.fn(),
  finalize: vi.fn(),
}));

vi.mock("vega-embed", () => ({
  default: embed,
}));

const encoder = new TextEncoder();

beforeEach(() => {
  embed.mockReset();
  finalize.mockReset();
});

describe("vegaLiteLoader", () => {
  test("loads a verified spec and adapts renderer cleanup to dispose", async () => {
    const spec = {
      $schema: "https://vega.github.io/schema/vega-lite/v6.1.0.json",
      data: { values: [{ symbol: "AAPL", price: 210.5 }] },
      mark: "bar",
      encoding: {
        x: { field: "symbol", type: "nominal" },
        y: { field: "price", type: "quantitative" },
      },
    };
    const format = await fixture(encoder.encode(JSON.stringify(spec)));
    const chart = await format.load(vegaLiteLoader({ actions: false }));
    const host = testHost();
    embed.mockResolvedValueOnce({ finalize });

    const mounted = await chart.mount(host.element, { renderer: "svg" });

    expect(chart.spec).toEqual(spec);
    expect(embed).toHaveBeenCalledWith(host.lastContainer().element, spec, {
      renderer: "svg",
      actions: false,
    });
    await mounted.dispose();
    await mounted.dispose();
    expect(finalize).toHaveBeenCalledOnce();
    expect(host.replaceChildren).toHaveBeenCalledOnce();
    expect(host.lastContainer().removeClasses).toHaveBeenCalledWith("vega-embed", "has-actions");
  });

  test("mounts through a loader registered with the publication", async () => {
    const spec = { mark: "point", data: { values: [] } };
    embed.mockResolvedValueOnce({ finalize });
    const format = await fixture(
      encoder.encode(JSON.stringify(spec)),
      vegaLiteLoader({ actions: false }),
    );
    const host = testHost();

    const mounted = await format.mount(host.element);

    expect(embed).toHaveBeenCalledWith(host.lastContainer().element, spec, {
      renderer: "canvas",
      actions: false,
    });
    await mounted.dispose();
  });

  test("allows disposal to retry when Vega finalization fails", async () => {
    const spec = { mark: "point", data: { values: [] } };
    finalize.mockImplementationOnce(() => {
      throw new Error("finalize failed");
    });
    embed.mockResolvedValueOnce({ finalize });
    const format = await fixture(encoder.encode(JSON.stringify(spec)));
    const chart = await format.load(vegaLiteLoader());
    const host = testHost();
    const mounted = await chart.mount(host.element);

    expect(() => mounted.dispose()).toThrow("finalize failed");
    await mounted.dispose();
    await mounted.dispose();
    expect(finalize).toHaveBeenCalledTimes(2);
  });

  test("settles cancellation while embedding and finalizes a late result", async () => {
    const spec = { mark: "point", data: { values: [] } };
    let resolveEmbed!: (value: { finalize: typeof finalize }) => void;
    const host = testHost();
    embed.mockImplementationOnce(
      (element) =>
        new Promise((resolve) => {
          host.appendPartialDom(element);
          resolveEmbed = resolve;
        }),
    );
    const format = await fixture(encoder.encode(JSON.stringify(spec)));
    const chart = await format.load(vegaLiteLoader());
    const controller = new AbortController();
    const mounting = chart.mount(host.element, {
      signal: controller.signal,
    });
    await vi.waitFor(() => expect(embed).toHaveBeenCalledOnce());

    controller.abort();

    await expect(mounting).rejects.toMatchObject({ name: "AbortError" });
    expect(host.childCount()).toBe(0);
    expect(host.classes()).toEqual([]);
    resolveEmbed({ finalize });
    await Promise.resolve();
    await Promise.resolve();
    expect(finalize).toHaveBeenCalledOnce();
  });

  test("does not clear a newer mount when a cancelled embed resolves late", async () => {
    const spec = { mark: "point", data: { values: [] } };
    const firstFinalize = vi.fn();
    const secondFinalize = vi.fn();
    let resolveFirst!: (value: { finalize: typeof firstFinalize }) => void;
    const host = testHost();
    embed
      .mockImplementationOnce(
        (element) =>
          new Promise((resolve) => {
            host.appendPartialDom(element);
            resolveFirst = resolve;
          }),
      )
      .mockImplementationOnce(async (element) => {
        host.appendPartialDom(element);
        return { finalize: secondFinalize };
      });
    const format = await fixture(encoder.encode(JSON.stringify(spec)));
    const chart = await format.load(vegaLiteLoader());
    const controller = new AbortController();
    const firstMount = chart.mount(host.element, { signal: controller.signal });
    await vi.waitFor(() => expect(embed).toHaveBeenCalledOnce());
    controller.abort();
    await expect(firstMount).rejects.toMatchObject({ name: "AbortError" });

    const secondMount = await chart.mount(host.element);
    const secondContainer = host.lastContainer();
    expect(host.childCount()).toBe(1);

    resolveFirst({ finalize: firstFinalize });
    await Promise.resolve();
    await Promise.resolve();

    expect(firstFinalize).toHaveBeenCalledOnce();
    expect(host.childCount()).toBe(1);
    expect(host.lastContainer()).toBe(secondContainer);
    expect(secondContainer.childCount()).toBe(1);

    await secondMount.dispose();
    expect(secondFinalize).toHaveBeenCalledOnce();
    expect(host.childCount()).toBe(0);
  });

  test("rejects a mismatched Vega-Lite media type", async () => {
    const format = await fixture(
      encoder.encode(JSON.stringify({ mark: "point" })),
      undefined,
      "application/json",
    );

    await expect(format.load(vegaLiteLoader())).rejects.toThrow("Vega-Lite JSON media type");
  });
});

async function fixture(
  data: Uint8Array,
  loader?: ReturnType<typeof vegaLiteLoader>,
  mediaType = "application/vnd.vegalite.v6+json",
): Promise<PublishedFormat> {
  const formatId = "vegalite.v1";
  const envelope = encode({
    data,
    media_type: mediaType,
    filename: null,
    metadata: { format_id: formatId, metadata_json: encoder.encode("{}") },
  });
  const sha256 = await digest(envelope);
  const key = "C_vegalite/return.bin";
  const index = {
    schema: "marimo-export.publication.v1",
    asset_codec: "marimo.blob-asset.msgpack.v1",
    notebook: { filename: "fixture.py", document_sha256: "a".repeat(64) },
    producer: { marimo: "0.24.0", marimo_export: "0.0.0" },
    variants: {
      current: {
        controls: {},
        outputs: {
          chart: {
            formats: {
              vegalite: {
                format_id: formatId,
                media_type: mediaType,
                metadata: {},
                asset: { key, sha256, size: envelope.byteLength },
              },
            },
          },
        },
      },
    },
  };
  const fetch: typeof globalThis.fetch = async (input) => {
    const url = input instanceof Request ? input.url : input.toString();
    if (url.endsWith("/index.json")) return new Response(JSON.stringify(index));
    if (url.endsWith(`/cache/${key}`)) return new Response(new Uint8Array(envelope));
    return new Response(null, { status: 404 });
  };
  const publication = await openPublication("https://example.test/export/", {
    fetch,
    ...(loader === undefined ? {} : { loaders: [loader] }),
  });
  return publication.variant("current").output("chart").format("vegalite");
}

function testHost() {
  const created: TestNode[] = [];
  const ownerDocument = {
    createElement() {
      const node = createNode();
      created.push(node);
      return node.element;
    },
  };
  const createNode = (): TestNode => {
    const children: unknown[] = [];
    const classes = new Set<string>();
    let parent: TestNode | undefined;
    const replaceChildren = vi.fn((...next: unknown[]) => {
      for (const child of children) {
        const childNode = nodeFor(child);
        if (childNode !== undefined) childNode.setParent(undefined);
      }
      children.splice(0, children.length, ...next);
      for (const child of next) nodeFor(child)?.setParent(node);
    });
    const removeClasses = vi.fn((...names: string[]) => {
      for (const name of names) classes.delete(name);
    });
    const element = {
      ownerDocument,
      replaceChildren,
      classList: {
        add(...names: string[]) {
          for (const name of names) classes.add(name);
        },
        remove: removeClasses,
      },
      remove() {
        parent?.removeChild(element);
      },
    } as unknown as HTMLElement;
    const node: TestNode = {
      element,
      children,
      classes,
      replaceChildren,
      removeClasses,
      setParent(value) {
        parent = value;
      },
      removeChild(child) {
        const index = children.indexOf(child);
        if (index >= 0) children.splice(index, 1);
        nodeFor(child)?.setParent(undefined);
      },
      childCount: () => children.length,
    };
    return node;
  };
  const root = createNode();
  const nodeFor = (value: unknown): TestNode | undefined =>
    [root, ...created].find((node) => node.element === value);
  return {
    element: root.element,
    appendPartialDom(element: HTMLElement) {
      const target = nodeFor(element)!;
      target.children.push({});
      target.classes.add("vega-embed");
      target.classes.add("has-actions");
    },
    childCount: root.childCount,
    classes: () => [...(created.at(-1)?.classes ?? [])],
    replaceChildren: root.replaceChildren,
    lastContainer: () => created.at(-1)!,
  };
}

interface TestNode {
  readonly element: HTMLElement;
  readonly children: unknown[];
  readonly classes: Set<string>;
  readonly replaceChildren: ReturnType<typeof vi.fn>;
  readonly removeClasses: ReturnType<typeof vi.fn>;
  setParent(parent: TestNode | undefined): void;
  removeChild(child: unknown): void;
  childCount(): number;
}

async function digest(bytes: Uint8Array): Promise<string> {
  const value = await crypto.subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>);
  return [...new Uint8Array(value)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
