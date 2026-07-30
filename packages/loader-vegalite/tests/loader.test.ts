import { encode } from "@msgpack/msgpack";
import { openExport } from "@marimo-team/marimo-export";
import type { ExportOutput } from "@marimo-team/marimo-export";
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
      "application/json",
    );

    await expect(format.load(vegaLiteLoader())).rejects.toThrow("No OutputLoader accepts");
  });
});

async function fixture(
  data: Uint8Array,
  mediaType = "application/vnd.vegalite.v6+json",
): Promise<ExportOutput> {
  const envelope = encode({
    data,
    media_type: mediaType,
    filename: null,
    metadata: {},
  });
  const sha256 = await digest(envelope);
  const fingerprint = await digest(encoder.encode("{}"));
  const index = {
    inputs: [],
    notebook: { filename: "fixture.py", document_sha256: "a".repeat(64) },
    outputs: ["chart"],
    producer: { marimo: "0.24.0", marimo_export: "0.0.0" },
    schema: "marimo-export.export.v1",
    states: {
      current: {
        fingerprint,
        inputs: {},
        outputs: {
          chart: {
            asset: { sha256, size: envelope.byteLength },
            codec: "marimo.blob-asset.msgpack.v1",
            filename: null,
            media_type: mediaType,
            metadata: {},
            provenance: {
              cache_key: "cell_cache/O_chart.json",
              python_type: "marimo._save.cache.BlobAsset",
              return_reference: "cell_cache/O_chart/return.bin",
            },
          },
        },
      },
    },
  };
  const fetch: typeof globalThis.fetch = async (input) => {
    const url = input instanceof Request ? input.url : input.toString();
    if (url.endsWith("/index.json")) return new Response(canonicalJson(index));
    if (url.endsWith(`/assets/${sha256}.bin`)) {
      return new Response(new Uint8Array(envelope));
    }
    return new Response(null, { status: 404 });
  };
  const notebookExport = await openExport("https://example.test/export/", { fetch });
  return notebookExport.state("current").output("chart");
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key])}`)
    .join(",")}}`;
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
