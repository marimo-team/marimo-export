import { memorySource, openExport } from "@marimo-team/marimo-export";
import { describe, expect, test } from "vite-plus/test";

import { parquet } from "../src/index.js";

const encoder = new TextEncoder();
const PARQUET =
  "UEFSMRUEFSAVIEwVBBUAEgAABAAAAEFBUEwEAAAATVNGVBUAFRIVEiwVBBUQFQYVBhw2ACgETVNGVBgEQUFQTBERAAAAAgAAAAQBAQMCFQQVIBUgTBUEFQASAAAAAAAAAFBqQAAAAAAARHpAFQAVEhUSLBUEFRAVBhUGHBgIAAAAAABEekAYCAAAAAAAUGpAFgAoCAAAAAAARHpAGAgAAAAAAFBqQBERAAAAAgAAAAQBAQMCFQQVIBUgTBUEFQASAAAKAAAAAAAAABQAAAAAAAAAFQAVEhUSLBUEFRAVBhUGHBgIFAAAAAAAAAAYCAoAAAAAAAAAFgAoCBQAAAAAAAAAGAgKAAAAAAAAABERAAAAAgAAAAQBAQMCFQQZTDUAGAZzY2hlbWEVBgAVDCUCGAZzeW1ib2wlAEwcAAAAFQolAhgFcHJpY2UAFQQlAhgGdm9sdW1lABYEGRwZPCYAHBUMGTUABhAZGAZzeW1ib2wVABYEFpQBFpQBJkQmCBw2ACgETVNGVBgEQUFQTBERABksFQQVABUCABUAFRAVAgA8FhAZBhkmAAQAAAAmABwVChk1AAYQGRgFcHJpY2UVABYEFswBFswBJtgBJpwBHBgIAAAAAABEekAYCAAAAAAAUGpAFgAoCAAAAAAARHpAGAgAAAAAAFBqQBERABksFQQVABUCABUAFRAVAgA8KQYZJgAEAAAAJgAcFQQZNQAGEBkYBnZvbHVtZRUAFgQWzAEWzAEmpAMm6AIcGAgUAAAAAAAAABgICgAAAAAAAAAWACgIFAAAAAAAAAAYCAoAAAAAAAAAEREAGSwVBBUAFQIAFQAVEBUCADwpBhkmAAQAAAAWrAQWBCYIFqwEABkcGAxBUlJPVzpzY2hlbWEYuAIvLy8vLytBQUFBQVFBQUFBQUFBS0FBd0FCZ0FGQUFnQUNnQUFBQUFCQkFBTUFBQUFDQUFJQUFBQUJBQUlBQUFBQkFBQUFBTUFBQUNFQUFBQVFBQUFBQVFBQUFDWS8vLy9BQUFCQWhBQUFBQWdBQUFBQkFBQUFBQUFBQUFHQUFBQWRtOXNkVzFsQUFBSUFBd0FDQUFIQUFnQUFBQUFBQUFCUUFBQUFORC8vLzhBQUFFREVBQUFBQndBQUFBRUFBQUFBQUFBQUFVQUFBQndjbWxqWlFBR0FBZ0FCZ0FHQUFBQUFBQUNBQkFBRkFBSUFBWUFCd0FNQUFBQUVBQVFBQUFBQUFBQkJSQUFBQUFjQUFBQUJBQUFBQUFBQUFBR0FBQUFjM2x0WW05c0FBQUVBQVFBQkFBQUFBPT0AGCBwYXJxdWV0LWNwcC1hcnJvdyB2ZXJzaW9uIDI1LjAuMBk8HAAAHAAAHAAAAOwCAABQQVIx";

interface PriceRow {
  symbol: string;
  price: number;
}

describe("parquet", () => {
  test("decodes selected rows and columns from a verified Parquet file", async () => {
    const output = await fixture(base64(PARQUET), "dataframe.parquet.v1");

    const rows = await output.load(
      parquet<PriceRow>({ columns: ["symbol", "price"], rowStart: 1, rowEnd: 2 }),
    );

    expect(rows).toEqual([{ symbol: "MSFT", price: 420.25 }]);
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
              media_type: "application/vnd.apache.parquet",
              metadata: { rows: 2, columns: ["symbol", "price", "volume"] },
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

function base64(value: string): Uint8Array {
  return Uint8Array.from(atob(value), (character) => character.charCodeAt(0));
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new Uint8Array(bytes));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
