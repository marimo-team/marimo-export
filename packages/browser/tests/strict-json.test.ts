import { describe, expect, test, vi } from "vite-plus/test";

import { parseStrictJson, trimJsonWhitespace } from "../src/strict-json.js";

describe("strict JSON", () => {
  test("trims ASCII JSON whitespace without copying or consuming a BOM", () => {
    const bytes = Uint8Array.of(0x20, 0x09, 0x0a, 0x0d, 0xef, 0xbb, 0xbf, 0x7b, 0x7d, 0x20);
    const trimmed = trimJsonWhitespace(bytes);

    expect(trimmed).toEqual(Uint8Array.of(0xef, 0xbb, 0xbf, 0x7b, 0x7d));
    expect(trimmed.buffer).toBe(bytes.buffer);
  });
  test("rejects more than 100,000 values before JSON.parse allocates the document", () => {
    const parse = vi.spyOn(JSON, "parse");
    const text = `[${Array<string>(100_000).fill("null").join(",")}]`;

    try {
      expect(() => parseStrictJson(text)).toThrow("maximum value count");
      expect(parse).not.toHaveBeenCalled();
    } finally {
      parse.mockRestore();
    }
  });

  test("counts object keys toward the shared JSON value limit", () => {
    const parse = vi.spyOn(JSON, "parse");
    const properties = Array.from({ length: 50_000 }, (_, index) => `"k${index}":null`);
    const text = `{${properties.join(",")}}`;

    try {
      expect(() => parseStrictJson(text)).toThrow("maximum value count");
      expect(parse.mock.calls.some(([value]) => value === text)).toBe(false);
    } finally {
      parse.mockRestore();
    }
  });

  test("bounds and escapes duplicate-key diagnostics", () => {
    const key = `\u009b${"x".repeat(200_000)}`;
    const text = `{${JSON.stringify(key)}:1,${JSON.stringify(key)}:2}`;

    let message = "";
    try {
      parseStrictJson(text);
    } catch (error) {
      if (error instanceof Error) message = error.message;
    }

    expect(message).toContain("Duplicate object key");
    expect(message.length).toBeLessThan(320);
    expect(message).not.toContain("\u009b");
    expect(message).toContain("\\u009b");
    expect(message).toContain("...");
  });

  test.each(["1.00000000000000001", "9007199254740990.5", "9007199254740991.1", "1e-324"])(
    "rejects the fractional JSON number that JavaScript rounds to an integer: %s",
    (value) => {
      expect(() => parseStrictJson(value)).toThrow("loses its fractional component");
    },
  );

  test("accepts exact integral decimal and exponent forms", () => {
    expect(parseStrictJson("[1.0,1e0,1.5e1,-0.0,0e-999,-0.0e-999,0.000e-324]")).toEqual([
      1, 1, 15, -0, 0, -0, 0,
    ]);
  });

  test("enforces the JSON number lexeme boundary", () => {
    expect(parseStrictJson(`1.${"0".repeat(1_022)}`)).toBe(1);
    expect(() => parseStrictJson(`1.${"0".repeat(1_023)}`)).toThrow("maximum lexeme length");
  });

  test("rejects a huge JSON number before JSON.parse", () => {
    const parse = vi.spyOn(JSON, "parse");
    try {
      expect(() => parseStrictJson(`1.${"0".repeat(1_000_000)}`)).toThrow("maximum lexeme length");
      expect(parse).not.toHaveBeenCalled();
    } finally {
      parse.mockRestore();
    }
  });

  test("decodes a large scalar string once", () => {
    const text = JSON.stringify("x".repeat(16 * 1024 * 1024));
    const parse = vi.spyOn(JSON, "parse");
    try {
      expect(parseStrictJson(text)).toHaveLength(16 * 1024 * 1024);
      expect(parse).toHaveBeenCalledOnce();
      expect(parse).toHaveBeenCalledWith(text);
    } finally {
      parse.mockRestore();
    }
  });
});
