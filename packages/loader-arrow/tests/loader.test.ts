import type { ArrowDescriptor, MediaType } from "@marimo-team/marimo-export";
import { CompressionType, tableFromArrays, tableToIPC } from "@uwdata/flechette";
import { describe, expect, test } from "vite-plus/test";

import { arrowTableLoader } from "../src/index.js";

const polarsStream = Uint8Array.from(
  atob(
    "/////6gAAAAEAAAA8v///xQAAAAEAAEAAAAKAAsACAAKAAQA+P///wwAAAAIAAgAAAAEAAIAAAA4AAAABAAAALz///8gAAAAEAAAAAgAAAABAwAAAAAAAPr///8CAAYABgAEAAUAAAB2YWx1ZQAAAOz///8sAAAAIAAAABgAAAABGAAAEAASAAQAEAARAAgAAAAMAAAAAAD8////BAAEAAgAAABjYXRlZ29yeQAAAAD/////yAAAAAQAAADs////gAAAAAAAAAAUAAAABAADAAwAEwAQABIADAAEAOb///8CAAAAAAAAAHQAAAAoAAAAFAAAAAAADgAYAAQADAAQAAAAFAABAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAEAAAAAAAAAAAAAAAAgAAAAIAAAAAAAAAAAAAAAAAAAACAAAAAAAAAAAAAAAAAAAAAwAAAG9uZQAAAAAAAAAAAAMAAAB0d28AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPg/AAAAAAAABEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD/////AAAAAA==",
  ),
  (character) => character.charCodeAt(0),
);

const descriptor: ArrowDescriptor = {
  asset: { sha256: "a".repeat(64), size: 0 },
  codec: "apache.arrow.file.v1",
  mediaType: "application/vnd.apache.arrow.file",
  provenance: {
    pythonType: "polars.dataframe.frame.DataFrame",
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
  test("decodes an uncompressed Polars Arrow IPC stream", async () => {
    const result = await arrowTableLoader().load({
      descriptor,
      mediaType,
      payload: polarsStream,
    });

    expect(result.toArray()).toEqual([
      { category: "one", value: 1.5 },
      { category: "two", value: 2.5 },
    ]);
  });

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
