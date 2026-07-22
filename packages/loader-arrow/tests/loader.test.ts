import { tableFromArrays, tableToIPC } from "@uwdata/flechette";
import { memorySource, openExport } from "@marimo-team/marimo-export";
import { describe, expect, test } from "vite-plus/test";

import { arrow } from "../src/index.js";

const encoder = new TextEncoder();

interface PriceRow {
  symbol: string;
  price: number;
}

describe("arrow", () => {
  test("decodes a verified Arrow IPC stream into a Flechette table", async () => {
    const bytes = tableToIPC(
      tableFromArrays({
        symbol: ["AAPL", "MSFT"],
        price: [210.5, 420.25],
      }),
      { format: "stream" },
    );
    expect(bytes).not.toBeNull();

    const output = await fixture(bytes!, "dataframe.arrow.v1");
    const table = await output.load(arrow<PriceRow>());

    expect(table.numRows).toBe(2);
    expect(table.names).toEqual(["symbol", "price"]);
    expect(table.toArray()).toEqual([
      { symbol: "AAPL", price: 210.5 },
      { symbol: "MSFT", price: 420.25 },
    ]);
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
              media_type: "application/vnd.apache.arrow.stream",
              metadata: { rows: 2, columns: ["symbol", "price"] },
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
