import { memorySource, openExport } from "@marimo-team/marimo-export";
import { describe, expect, test, vi } from "vite-plus/test";

import { vegaLite } from "../src/index.js";

const { embed, finalize } = vi.hoisted(() => ({
  embed: vi.fn(),
  finalize: vi.fn(),
}));

vi.mock("vega-embed", () => ({
  default: embed,
}));

const encoder = new TextEncoder();

describe("vegaLite", () => {
  test("loads a verified spec and exposes the renderer cleanup lifecycle", async () => {
    const spec = {
      $schema: "https://vega.github.io/schema/vega-lite/v6.1.0.json",
      data: { values: [{ symbol: "AAPL", price: 210.5 }] },
      mark: "bar",
      encoding: {
        x: { field: "symbol", type: "nominal" },
        y: { field: "price", type: "quantitative" },
      },
    };
    const output = await fixture(encoder.encode(JSON.stringify(spec)), "vegalite.v1");
    expect(output.mediaType).toBe("application/vnd.vegalite.v6+json");
    const chart = await output.load(vegaLite({ actions: false }));
    const host = Object.create(null) as HTMLElement;
    embed.mockResolvedValueOnce({ finalize });

    const mounted = await chart.mount(host, { renderer: "svg" });

    expect(chart.spec).toEqual(spec);
    expect(embed).toHaveBeenCalledWith(host, spec, {
      renderer: "svg",
      actions: false,
    });
    mounted.finalize();
    expect(finalize).toHaveBeenCalledOnce();
  });
});

async function fixture(payload: Uint8Array, formatId: string) {
  const payloadSha = await sha256(payload);
  const key = `marimo-export/payloads/sha256/${payloadSha}`;
  const index = {
    schema: "marimo-export.index.v1",
    notebook: { name: "fixture.py", source_sha256: "a".repeat(64) },
    plan_sha256: "b".repeat(64),
    producer: { marimo_version: "0.23.14", marimo_export_version: "0.0.0" },
    scenarios: [
      {
        id: "case",
        inputs: {},
        outputs: {
          value: {
            format: {
              format_id: formatId,
              media_type: "application/vnd.vegalite.v6+json",
              metadata: {},
              payload: { key, sha256: payloadSha, size: payload.byteLength },
            },
          },
        },
      },
    ],
  };
  const indexBytes = encoder.encode(JSON.stringify(index));
  const published = await openExport(
    memorySource({ "index.json": indexBytes, [`cache/${key}`]: payload }),
  );
  return published.scenario("case").output("value", "format");
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new Uint8Array(bytes));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
