import type { BlobAssetLoadInput } from "@marimo-team/marimo-export";
import { beforeEach, describe, expect, test, vi } from "vite-plus/test";

import { parquetRowsLoader } from "../src/index.js";

const { read } = vi.hoisted(() => ({ read: vi.fn() }));

vi.mock("hyparquet", () => ({ parquetReadObjects: read }));

beforeEach(() => read.mockReset());

describe("parquetRowsLoader", () => {
  test("selects Parquet media types and passes verified bytes to Hyparquet", async () => {
    const loader = parquetRowsLoader({ columns: ["symbol"], rowStart: 1, rowEnd: 3 });
    read.mockResolvedValueOnce([{ symbol: "AAPL" }]);

    const rows = await loader.load(input(new Uint8Array([80, 65, 82, 49])));

    expect(rows).toEqual([{ symbol: "AAPL" }]);
    expect(Object.isFrozen(rows)).toBe(true);
    expect(read).toHaveBeenCalledOnce();
    const options = read.mock.calls[0]![0] as Record<string, unknown>;
    expect(options).toMatchObject({ columns: ["symbol"], rowStart: 1, rowEnd: 3 });
    expect(options.file).toBeInstanceOf(ArrayBuffer);
    expect(new Uint8Array(options.file as ArrayBuffer)).toEqual(new Uint8Array([80, 65, 82, 49]));
    expect(loader.accepts(input().descriptor, media("application/vnd.apache.parquet"))).toBe(true);
    expect(loader.accepts(input().descriptor, media("application/x-parquet"))).toBe(true);
    expect(loader.accepts(input().descriptor, media("application/octet-stream"))).toBe(false);
  });

  test("honors abort during decoding", async () => {
    let resolve!: (rows: Record<string, unknown>[]) => void;
    read.mockReturnValueOnce(
      new Promise<Record<string, unknown>[]>((complete) => {
        resolve = complete;
      }),
    );
    const during = new AbortController();
    const pending = parquetRowsLoader().load({ ...input(), signal: during.signal });
    during.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    resolve([]);
  });
});

function input(data = new Uint8Array()): BlobAssetLoadInput {
  const parsed = media("application/vnd.apache.parquet");
  return {
    descriptor: {
      asset: { sha256: "a".repeat(64), size: data.byteLength },
      codec: "marimo.blob-asset.msgpack.v1",
      filename: "table.parquet",
      mediaType: parsed.raw,
      metadata: {},
      provenance: {
        cacheKey: "cell_cache/P_table.json",
        pythonType: "marimo._save.cache.BlobAsset",
        returnReference: "cell_cache/P_table/return.bin",
      },
    },
    mediaType: parsed,
    payload: {
      data,
      mediaType: parsed,
      filename: "table.parquet",
      metadata: {},
    },
  };
}

function media(value: string) {
  const [type, subtype] = value.split("/") as [string, string];
  return {
    raw: value,
    essence: value,
    type,
    subtype,
    parameters: new Map<string, string>(),
  };
}
