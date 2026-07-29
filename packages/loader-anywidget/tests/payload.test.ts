import { afterEach, describe, expect, test, vi } from "vite-plus/test";

import { parseAnyWidgetPayload, parseDataUrl } from "../src/payload.js";
import { moduleUrl, notification, payload } from "./fixture.js";

const MIB = 1024 * 1024;

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AnyWidget payload resources", () => {
  test.each([
    ["", 0],
    ["AAAA", 3],
    ["AA==", 1],
    ["AAA=", 2],
    ["////", 3],
  ])("decodes canonical base64 %s", (encoded, expectedSize) => {
    expect(parseBuffer(encoded).byteLength).toBe(expectedSize);
  });

  test.each(["A", "AAAA=", "A===", "AA=A", "AA/!", "AB==", "AAB="])(
    "rejects noncanonical base64 %s",
    (encoded) => {
      expect(() => parseBuffer(encoded)).toThrow("not canonical base64");
    },
  );

  test("validates a large base64 buffer before decoding", () => {
    const decode = vi.spyOn(globalThis, "atob");
    const encoded = `${"A".repeat(8 * MIB - 1)}*`;

    expect(() => parseBuffer(encoded)).toThrow("not canonical base64");
    expect(decode).not.toHaveBeenCalled();
  });

  test("parses a 9 MiB semicolon-heavy data URL header", () => {
    const parsed = parseDataUrl(
      `data:text/javascript${";".repeat(9 * MIB)}BASE64,AAAA`,
      "large data URL",
    );

    expect(parsed).toEqual({
      body: "AAAA",
      isBase64: true,
      mediaType: "text/javascript",
    });
  });

  test.each([
    ["ASCII", "a".repeat(1_024)],
    ["multibyte", "é".repeat(512)],
  ])("accepts a data URL media type at 1,024 UTF-8 bytes with %s input", (_name, mediaType) => {
    expect(parseDataUrl(`data:${mediaType},body`, "data URL").mediaType).toBe(mediaType);
  });

  test.each([
    ["ASCII", "a".repeat(1_025)],
    ["multibyte", `${"é".repeat(512)}a`],
  ])("rejects a data URL media type beyond 1,024 UTF-8 bytes with %s input", (_name, mediaType) => {
    expect(() => parseDataUrl(`data:${mediaType},body`, "data URL")).toThrow(
      "data URL media type exceeds 1024 UTF-8 bytes",
    );
  });

  test.each([
    ["ASCII", externalEsmUrl(8_192, "a")],
    ["multibyte", externalEsmUrl(8_192, "é")],
  ])("accepts an external ESM URL at 8,192 UTF-8 bytes with %s input", (_name, url) => {
    expect(() => parseModuleUrl(url)).not.toThrow();
  });

  test.each([
    ["ASCII", externalEsmUrl(8_193, "a")],
    ["multibyte", externalEsmUrl(8_193, "é")],
  ])("rejects an external ESM URL beyond 8,192 UTF-8 bytes with %s input", (_name, url) => {
    expect(() => parseModuleUrl(url)).toThrow("contains an invalid ESM URL");
  });

  test("bounds and escapes an oversized external ESM URL diagnostic", () => {
    const prefix = "https://example.test/\u009b";
    const url = `${prefix}${"x".repeat(8_193 - prefix.length)}`;

    const message = errorMessage(() => parseModuleUrl(url));

    expect(message.length).toBeLessThan(256);
    expect(message).toContain("\\u009b");
    expect(message).not.toContain("\u009b");
    expect(message).toContain("...");
  });

  test("validates an inline data ESM URL before the external URL bound", () => {
    const url = `DATA:text/javascript,${"x".repeat(8_192)}`;

    expect(() => parseModuleUrl(url)).not.toThrow();
  });

  test("bounds an incompatible protocol diagnostic at the external URL limit", () => {
    const url = `${"a".repeat(8_190)}:x`;

    const message = errorMessage(() => parseModuleUrl(url));

    expect(message.length).toBeLessThan(256);
    expect(message).toContain("uses incompatible ESM URL protocol");
    expect(message).toContain("...");
  });

  test("validates a large percent-encoded body before decoding", () => {
    const decode = vi.spyOn(globalThis, "decodeURIComponent");

    expect(() => parseModuleUrl(`data:,${"%41".repeat(3 * MIB)}`)).not.toThrow();
    expect(decode).not.toHaveBeenCalled();
  });

  test.each(["%", "%ZZ", "%C3", "%C3x%A9", "%C3é", "%ED%A0%80", "%F4%90%80%80"])(
    "rejects malformed percent-encoded data %s",
    (body) => {
      expect(() => parseModuleUrl(`data:,${body}`)).toThrow("malformed percent-encoded data");
    },
  );

  test("accepts percent-encoded data across a literal Unicode boundary", () => {
    expect(() => parseModuleUrl("data:,é%C3%A9")).not.toThrow();
  });
});

function parseBuffer(encoded: string): DataView {
  const snapshot = parseAnyWidgetPayload(
    payload({
      modelNotifications: [
        notification({
          id: "model-0",
          state: { binary: null },
          moduleUrl: moduleUrl("export default {}"),
          bufferPaths: [["binary"]],
          buffers: [encoded],
        }),
      ],
    }),
  );
  return snapshot.models.get("model-0")!.state.binary as DataView;
}

function parseModuleUrl(url: string): void {
  parseAnyWidgetPayload(
    payload({
      modelNotifications: [notification({ id: "model-0", state: {}, moduleUrl: url })],
    }),
  );
}

function externalEsmUrl(byteLength: number, fill: "a" | "é"): string {
  const prefix = "https://example.test/";
  const remaining = byteLength - prefix.length;
  if (fill === "a") return `${prefix}${fill.repeat(remaining)}`;
  return `${prefix}${fill.repeat(Math.floor(remaining / 2))}${remaining % 2 === 0 ? "" : "a"}`;
}

function errorMessage(operation: () => void): string {
  try {
    operation();
  } catch (error) {
    if (error instanceof Error) return error.message;
  }
  return "";
}
