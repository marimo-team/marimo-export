import type { BlobAssetLoadInput } from "@marimo-team/marimo-export";
import { describe, expect, test, vi } from "vite-plus/test";

import type { ParquetObjectReader, ParquetRow } from "../src/index.js";
import { parquetRowsLoaderWith } from "../src/index.js";

const read = vi.fn<ParquetObjectReader>();

describe("parquetRowsLoader", () => {
  test("selects Parquet media types and passes verified bytes to Hyparquet", async () => {
    read.mockReset();
    const loader = parquetRowsLoaderWith(read, { columns: ["symbol"], rowStart: 1, rowEnd: 3 });
    read.mockResolvedValueOnce([{ symbol: "AAPL" }]);

    const rows = await loader.load(input(new Uint8Array([80, 65, 82, 49])));

    expect(rows).toEqual([{ symbol: "AAPL" }]);
    expect(read).toHaveBeenCalledOnce();
    const options = read.mock.calls[0]![0];
    expect(options).toMatchObject({ columns: ["symbol"], rowStart: 1, rowEnd: 3 });
    expect(options.file).toBeInstanceOf(ArrayBuffer);
    if (!(options.file instanceof ArrayBuffer)) throw new TypeError("Fixture file must be bytes.");
    expect(new Uint8Array(options.file)).toEqual(new Uint8Array([80, 65, 82, 49]));
    expect(loader.accepts(input().descriptor, media("application/vnd.apache.parquet"))).toBe(true);
    expect(loader.accepts(input().descriptor, media("application/x-parquet"))).toBe(true);
    expect(loader.accepts(input().descriptor, media("application/octet-stream"))).toBe(false);
  });

  test("honors abort during decoding", async () => {
    read.mockReset();
    let resolve!: (rows: ParquetRow[]) => void;
    read.mockReturnValueOnce(
      new Promise<ParquetRow[]>((complete) => {
        resolve = complete;
      }),
    );
    const during = new AbortController();
    const pending = parquetRowsLoaderWith(read).load({ ...input(), signal: during.signal });
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
        pythonType: "marimo._save.cache.BlobAsset",
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
  const [type, subtype] = value.split("/");
  if (type === undefined || subtype === undefined) throw new TypeError("Media type is incomplete.");
  return {
    raw: value,
    essence: value,
    type,
    subtype,
    parameters: new Map<string, string>(),
  };
}
