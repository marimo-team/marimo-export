import { encode } from "@msgpack/msgpack";
import { describe, expect, test } from "vite-plus/test";

import { decodeBlobAssetWire } from "../src/strict-msgpack.js";

const encoder = new TextEncoder();

describe("canonical BlobAsset MessagePack", () => {
  test.each([0, 0xff, 0x100, 0xffff, 0x10000])(
    "accepts the canonical binary prefix at length %d",
    (length) => {
      const data = new Uint8Array(length);
      const decoded = decodeBlobAssetWire(envelope(data));

      expect(decoded.data).toEqual(data);
    },
  );

  test.each([1, 0x1f, 0x20, 0xff, 0x100])(
    "accepts the canonical string prefix at length %d",
    (length) => {
      const mediaType = "x".repeat(length);
      const decoded = decodeBlobAssetWire(envelope(undefined, mediaType));

      expect(decoded.mediaType).toBe(mediaType);
    },
  );

  test("uses UTF-8 byte length for MessagePack strings", () => {
    const mediaType = "é".repeat(16);

    expect(decodeBlobAssetWire(envelope(undefined, mediaType)).mediaType).toBe(mediaType);
  });

  test("decodes a large data payload as a view of the envelope", () => {
    const dataLength = 16 * 1024 * 1024;
    const header = Uint8Array.of(0x84, 0xa4, 0x64, 0x61, 0x74, 0x61, 0xc6, 0x01, 0x00, 0x00, 0x00);
    const trailer = encode({
      media_type: "application/json",
      filename: null,
      metadata: { format_id: "json.v1", metadata_json: encoder.encode("{}") },
    }).subarray(1);
    const envelope = new Uint8Array(header.byteLength + dataLength + trailer.byteLength);
    envelope.set(header);
    envelope.set(trailer, header.byteLength + dataLength);

    const decoded = decodeBlobAssetWire(envelope);

    expect(decoded.data.byteLength).toBe(dataLength);
    expect(decoded.data.byteOffset).toBe(header.byteLength);
    expect(decoded.data.buffer).toBe(envelope.buffer);
    expect(decoded.metadataJson.buffer).toBe(envelope.buffer);
  });

  test.each([
    ["map16 root", Uint8Array.of(0xde, 0x00, 0x04), 0, 1],
    ["str8 field name", Uint8Array.of(0xd9, 0x04), 1, 1],
    ["bin16 data", Uint8Array.of(0xc5, 0x00, 0x02), 6, 2],
  ])("rejects the noncanonical %s encoding", (_label, replacement, start, length) => {
    const canonical = envelope();
    const noncanonical = replace(canonical, start, length, replacement);

    expect(() => decodeBlobAssetWire(noncanonical)).toThrow();
  });

  test("rejects nonminimal nested map, string, and binary prefixes", () => {
    const canonical = envelope();
    const metadataMap = find(canonical, Uint8Array.of(0xa8, ...ascii("metadata"), 0x82)) + 9;
    const formatId = find(canonical, Uint8Array.of(0xa9, ...ascii("format_id"), 0xa7)) + 10;
    const metadataJson = find(canonical, Uint8Array.of(0xad, ...ascii("metadata_json"), 0xc4)) + 14;

    expect(() =>
      decodeBlobAssetWire(replace(canonical, metadataMap, 1, Uint8Array.of(0xde, 0x00, 0x02))),
    ).toThrow();
    expect(() =>
      decodeBlobAssetWire(replace(canonical, formatId, 1, Uint8Array.of(0xd9, 0x07))),
    ).toThrow();
    expect(() =>
      decodeBlobAssetWire(replace(canonical, metadataJson, 2, Uint8Array.of(0xc5, 0x00, 0x02))),
    ).toThrow();
  });

  test("rejects malformed UTF-8 in a MessagePack string", () => {
    const canonical = envelope();
    const mediaType = find(canonical, Uint8Array.of(0xaa, ...ascii("media_type"), 0xb0)) + 12;
    const malformed = new Uint8Array(canonical);
    malformed.set([0xc0, 0xaf], mediaType);

    expect(() => decodeBlobAssetWire(malformed)).toThrow();
  });
});

function envelope(
  data: Uint8Array = encoder.encode("ok"),
  mediaType = "application/json",
): Uint8Array {
  return encode({
    data,
    media_type: mediaType,
    filename: null,
    metadata: { format_id: "json.v1", metadata_json: encoder.encode("{}") },
  });
}

function replace(
  source: Uint8Array,
  start: number,
  length: number,
  replacement: Uint8Array,
): Uint8Array {
  const result = new Uint8Array(source.byteLength - length + replacement.byteLength);
  result.set(source.subarray(0, start));
  result.set(replacement, start);
  result.set(source.subarray(start + length), start + replacement.byteLength);
  return result;
}

function find(source: Uint8Array, pattern: Uint8Array): number {
  outer: for (let index = 0; index <= source.byteLength - pattern.byteLength; index += 1) {
    for (let offset = 0; offset < pattern.byteLength; offset += 1) {
      if (source[index + offset] !== pattern[offset]) continue outer;
    }
    return index;
  }
  throw new Error("MessagePack pattern is missing from the test fixture.");
}

function ascii(value: string): number[] {
  return [...value].map((character) => character.charCodeAt(0));
}
