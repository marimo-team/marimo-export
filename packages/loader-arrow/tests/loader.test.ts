import type { ArrowDescriptor, MediaType } from "@marimo-team/marimo-export";
import {
  CompressionType,
  getCompressionCodec,
  tableFromArrays,
  tableToIPC,
} from "@uwdata/flechette";
import { describe, expect, test } from "vite-plus/test";

import { arrowTableLoader } from "../src/index.js";

const descriptor: ArrowDescriptor = {
  asset: { sha256: "a".repeat(64), size: 0 },
  codec: "apache.arrow.file.v1",
  mediaType: "application/vnd.apache.arrow.file",
  provenance: {
    cacheKey: "cell_cache/P_table.json",
    pythonType: "polars.dataframe.frame.DataFrame",
    returnReference: "cell_cache/P_table/return.arrow",
  },
};
const mediaType: MediaType = {
  raw: "application/vnd.apache.arrow.file",
  essence: "application/vnd.apache.arrow.file",
  type: "application",
  subtype: "vnd.apache.arrow.file",
  parameters: new Map(),
};

describe("arrowTableLoader", () => {
  test("registers and decodes LZ4-compressed Arrow files", async () => {
    const input = tableFromArrays({
      category: ["one", "two", "three"],
      value: new Float64Array([1.5, 2.5, 3.5]),
    });
    const registrationInput = tableToIPC(input, { format: "file" });
    if (registrationInput === null) throw new Error("Expected an in-memory Arrow file.");
    await arrowTableLoader().load({
      descriptor,
      mediaType,
      payload: registrationInput,
    });
    const compressed = tableToIPC(input, {
      codec: CompressionType.LZ4_FRAME,
      format: "file",
    });
    if (compressed === null) throw new Error("Expected an in-memory Arrow file.");

    const result = await arrowTableLoader().load({
      descriptor,
      mediaType,
      payload: compressed,
    });

    expect(getCompressionCodec(CompressionType.LZ4_FRAME)).not.toBeNull();
    expect(result.toArray()).toEqual([
      { category: "one", value: 1.5 },
      { category: "two", value: 2.5 },
      { category: "three", value: 3.5 },
    ]);
  });

  test("allows explicit numeric coercion and honors abort", async () => {
    const input = tableFromArrays({ id: new BigInt64Array([7n]) });
    const bytes = tableToIPC(input, { format: "file" });
    if (bytes === null) throw new Error("Expected an in-memory Arrow file.");
    const coerced = await arrowTableLoader({ extraction: { useBigInt: false } }).load({
      descriptor,
      mediaType,
      payload: bytes,
    });
    expect(coerced.toArray()).toEqual([{ id: 7 }]);

    const controller = new AbortController();
    controller.abort();
    expect(() =>
      arrowTableLoader().load({
        descriptor,
        mediaType,
        payload: bytes,
        signal: controller.signal,
      }),
    ).toThrow(expect.objectContaining({ name: "AbortError" }));
  });
});
