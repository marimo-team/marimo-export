import { describe, expect, test } from "vite-plus/test";

import { parsePublicationManifest } from "../src/schema.js";
import { indexFor } from "./fixture.js";

type MutableRecord = Record<string, unknown>;
type Mutation = (index: MutableRecord) => void;

const mutations: readonly (readonly [string, Mutation])[] = [
  ["schema", (index) => set(index, ["schema"], "marimo-export.publication.v2")],
  ["asset codec", (index) => set(index, ["asset_codec"], "raw.v1")],
  ["document digest", (index) => set(index, ["notebook", "document_sha256"], "short")],
  ["notebook filename", (index) => set(index, ["notebook", "filename"], "../finance.py")],
  ["producer name", (index) => set(index, ["producer", "marimo"], " 0.24.0")],
  ["unknown field", (index) => set(index, ["extra"], true)],
  ["missing field", (index) => remove(index, ["producer", "marimo"])],
  ["unsafe cache key", (index) => setAssetKey(index, "../secret.bin")],
  ["cache key alternate data stream", (index) => setAssetKey(index, "C/return.bin:secret.bin")],
  ["cache key Windows character", (index) => setAssetKey(index, "C/value?.bin")],
  ["cache key trailing component dot", (index) => setAssetKey(index, "C./return.bin")],
  ["cache key trailing component space", (index) => setAssetKey(index, "C /return.bin")],
  ["cache key reserved device basename", (index) => setAssetKey(index, "C/CON.bin")],
  ["cache key normalized device basename", (index) => setAssetKey(index, "C/CON .bin")],
  ["cache key superscript device basename", (index) => setAssetKey(index, "C/COM¹.bin")],
  ["cache key console device basename", (index) => setAssetKey(index, "C/CONOUT$.bin")],
  ["cache key component boundary whitespace", (index) => setAssetKey(index, "C/\u00a0value.bin")],
  [
    "cache key over 1024 bytes",
    (index) =>
      setAssetKey(
        index,
        `${"a".repeat(255)}/${"b".repeat(254)}/${"c".repeat(254)}/${"d".repeat(254)}/.bin`,
      ),
  ],
  ["cache key component over 255 bytes", (index) => setAssetKey(index, `${"é".repeat(126)}.bin`)],
  ["non-binary cache key", (index) => setAssetKey(index, "C_fixture/return.dat")],
  ["unsafe JSON integer", (index) => set(index, formatPath("metadata"), { count: 2 ** 53 })],
  ["format ID", (index) => set(index, formatPath("format_id"), "json/v1")],
  ["format ID length", (index) => set(index, formatPath("format_id"), "a".repeat(256))],
  ["media type", (index) => set(index, formatPath("media_type"), "json")],
  [
    "media type length",
    (index) => set(index, formatPath("media_type"), `application/${"a".repeat(1_013)}`),
  ],
  [
    "media type control",
    (index) => set(index, formatPath("media_type"), "text/plain; charset=utf-8\u0000"),
  ],
  ["media type DEL", (index) => set(index, formatPath("media_type"), "text/plain; x=\u007f")],
  ["media type non-ASCII", (index) => set(index, formatPath("media_type"), "text/plain; x=é")],
  ["control name", (index) => set(index, ["variants", "current", "controls"], { " control": 1 })],
  [
    "control name DEL",
    (index) => set(index, ["variants", "current", "controls"], { "bad\u007f": 1 }),
  ],
  ["variant name", (index) => rename(at(index, ["variants"]), "current", " current")],
  ["empty variants", (index) => set(index, ["variants"], {})],
  ["empty outputs", (index) => set(index, ["variants", "current", "outputs"], {})],
  ["empty formats", (index) => set(index, outputPath("formats"), {})],
];

describe("publication schema", () => {
  test("accepts the exact publication.v1 contract", () => {
    expect(parsePublicationManifest(indexFor())).toMatchObject({
      schema: "marimo-export.publication.v1",
      asset_codec: "marimo.blob-asset.msgpack.v1",
    });
  });

  test.each(mutations)("rejects %s", (_label, mutate) => {
    const index = structuredClone(indexFor()) as unknown as MutableRecord;
    mutate(index);
    expect(() => parsePublicationManifest(index)).toThrow();
  });

  test("rejects conflicting publication metadata for one cache key", () => {
    const index = structuredClone(indexFor()) as unknown as MutableRecord;
    const outputs = at(index, ["variants", "current", "outputs"]);
    outputs.copy = structuredClone(at(outputs, ["summary"]));
    set(outputs, ["copy", "formats", "json", "asset", "size"], 2);

    expect(() => parsePublicationManifest(index)).toThrow("conflicting publication metadata");
  });

  test("accepts recorded control names independently of Python Unicode tables", () => {
    const index = structuredClone(indexFor()) as unknown as MutableRecord;
    set(index, ["variants", "current", "controls"], {
      for: 1,
      "a²": 2,
      "\u088f": 3,
    });

    expect(parsePublicationManifest(index).variants.current?.controls).toEqual({
      for: 1,
      "a²": 2,
      "\u088f": 3,
    });
  });

  test("accepts a cache key at the 1024-byte boundary", () => {
    const index = structuredClone(indexFor()) as unknown as MutableRecord;
    const key = `${"a".repeat(254)}/${"b".repeat(254)}/${"c".repeat(254)}/${"d".repeat(254)}/.bin`;
    setAssetKey(index, key);

    expect(
      parsePublicationManifest(index).variants.current?.outputs.summary?.formats.json?.asset.key,
    ).toBe(key);
  });

  test("accepts a multibyte cache-key component at the 255-byte boundary", () => {
    const index = structuredClone(indexFor()) as unknown as MutableRecord;
    const key = `${"é".repeat(125)}a.bin`;
    setAssetKey(index, key);

    expect(
      parsePublicationManifest(index).variants.current?.outputs.summary?.formats.json?.asset.key,
    ).toBe(key);
  });

  test.each(["report?.py", "NUL.py", "report.py.", "report.py ", "folder\\report.py"])(
    "accepts the POSIX notebook provenance basename %j",
    (filename) => {
      const index = structuredClone(indexFor()) as unknown as MutableRecord;
      set(index, ["notebook", "filename"], filename);

      expect(parsePublicationManifest(index).notebook.filename).toBe(filename);
    },
  );

  test("matches Python boundary whitespace handling", () => {
    const accepted = structuredClone(indexFor()) as unknown as MutableRecord;
    rename(at(accepted, ["variants"]), "current", "\ufeffcurrent");
    expect(parsePublicationManifest(accepted).variants).toHaveProperty("\ufeffcurrent");

    const rejected = structuredClone(indexFor()) as unknown as MutableRecord;
    rename(at(rejected, ["variants"]), "current", "\u0085current");
    expect(() => parsePublicationManifest(rejected)).toThrow("surrounding whitespace");
  });

  test("accepts Unicode scalar values and rejects unpaired surrogates", () => {
    const accepted = structuredClone(indexFor()) as unknown as MutableRecord;
    set(accepted, formatPath("metadata"), { "\ud800\udc00": "\ud83d\udcc8" });
    expect(
      parsePublicationManifest(accepted).variants.current?.outputs.summary?.formats.json,
    ).toMatchObject({ metadata: { "\ud800\udc00": "\ud83d\udcc8" } });

    for (const [label, path, value] of [
      ["filename", ["notebook", "filename"], "finance\ud800.py"],
      ["metadata value", formatPath("metadata"), { value: "\ud800" }],
      ["metadata key", formatPath("metadata"), { ["\udc00"]: true }],
    ] as const) {
      const rejected = structuredClone(indexFor()) as unknown as MutableRecord;
      set(rejected, path, value);
      expect(() => parsePublicationManifest(rejected), label).toThrow("Unicode scalar values");
    }
  });

  test("rejects metadata beyond the shared JSON nesting limit", () => {
    const index = structuredClone(indexFor()) as unknown as MutableRecord;
    const metadata: MutableRecord = {};
    let current = metadata;
    for (let depth = 0; depth < 257; depth += 1) {
      const child: MutableRecord = {};
      current.child = child;
      current = child;
    }
    set(index, formatPath("metadata"), metadata);

    expect(() => parsePublicationManifest(index)).toThrow("maximum JSON nesting depth");
  });

  test("bounds diagnostics for deeply nested metadata with long keys", () => {
    const index = structuredClone(indexFor()) as unknown as MutableRecord;
    const metadata: MutableRecord = {};
    const longKey = "nested-key-".repeat(400);
    let current = metadata;
    for (let depth = 0; depth < 257; depth += 1) {
      const child: MutableRecord = {};
      current[longKey] = child;
      current = child;
    }
    set(index, formatPath("metadata"), metadata);

    const message = validationMessage(index);
    expect(message).toContain("maximum JSON nesting depth");
    expect(message).toContain("...");
    expect(message.length).toBeLessThanOrEqual(2_048);
  });

  test("escapes and bounds unknown field diagnostics", () => {
    const index = structuredClone(indexFor()) as unknown as MutableRecord;
    index["nul\u0000field"] = true;
    index["escape\u001bfield"] = true;
    index["delete\u007ffield"] = true;
    index["c1\u0080\u009bfield"] = true;
    index["long-".repeat(2_000)] = true;
    for (let field = 0; field < 12; field += 1) index[`extra-${field}`] = true;

    const message = validationMessage(index);
    expect(message).toContain('"nul\\u0000field"');
    expect(message).toContain('"escape\\u001bfield"');
    expect(message).toContain('"delete\\u007ffield"');
    expect(message).toContain('"c1\\u0080\\u009bfield"');
    expect(message).toMatch(/\.\.\. \(\+\d+ more\)/u);
    expect(hasDiagnosticControl(message)).toBe(false);
    expect(message.length).toBeLessThanOrEqual(2_048);
  });

  test("quotes and escapes dynamic metadata path segments", () => {
    const index = structuredClone(indexFor()) as unknown as MutableRecord;
    set(index, formatPath("metadata"), { "bad\u009bkey": 2 ** 53 });

    const message = validationMessage(index);
    expect(message).toContain('["bad\\u009bkey"]');
    expect(message).not.toContain("\u009b");
  });
});

function validationMessage(index: MutableRecord): string {
  try {
    parsePublicationManifest(index);
  } catch (error) {
    if (error instanceof Error) return error.message;
  }
  throw new Error("Expected publication validation to fail.");
}

function hasDiagnosticControl(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit <= 0x1f || (codeUnit >= 0x7f && codeUnit <= 0x9f)) return true;
  }
  return false;
}

function setAssetKey(index: MutableRecord, key: string): void {
  set(index, [...formatPath("asset"), "key"], key);
}

function formatPath(field: string): string[] {
  return [...outputPath("formats"), "json", field];
}

function outputPath(field: string): string[] {
  return ["variants", "current", "outputs", "summary", field];
}

function set(root: MutableRecord, path: readonly string[], value: unknown): void {
  const parent = at(root, path.slice(0, -1));
  parent[path.at(-1)!] = value;
}

function remove(root: MutableRecord, path: readonly string[]): void {
  const parent = at(root, path.slice(0, -1));
  Reflect.deleteProperty(parent, path.at(-1)!);
}

function rename(record: MutableRecord, from: string, to: string): void {
  record[to] = record[from];
  Reflect.deleteProperty(record, from);
}

function at(root: MutableRecord, path: readonly string[]): MutableRecord {
  let current = root;
  for (const part of path) {
    const next = current[part];
    if (typeof next !== "object" || next === null || Array.isArray(next)) {
      throw new TypeError(`Fixture path ${path.join(".")} is not an object.`);
    }
    current = next as MutableRecord;
  }
  return current;
}
