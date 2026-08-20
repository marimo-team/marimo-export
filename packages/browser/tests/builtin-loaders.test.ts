import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vite-plus/test";

import { htmlLoader } from "../src/loader/html.js";
import { jsonLoader } from "../src/loader/json.js";
import { marimoCellLoader } from "../src/loader/marimo-cell.js";
import { marimoOutputLoader } from "../src/loader/marimo-output.js";
import { textLoader } from "../src/loader/text.js";
import { canonicalJson } from "../src/schema.js";
import type { BlobAssetLoadInput, JsonValue, MediaType, OutputDescriptor } from "../src/types.js";

const encoder = new TextEncoder();
const projectionFixturePath = fileURLToPath(
  new URL("../../../tests/fixtures/export/projection-records.json", import.meta.url),
);

describe("built-in loaders", () => {
  test("returns detached immutable JSON", () => {
    const source = { rows: [{ value: 1 }] };

    const result = jsonLoader().load({
      descriptor: {
        codec: "marimo.json.v1",
        mediaType: "application/vnd.marimo.json.v1+json",
        provenance: { pythonType: "builtins.dict" },
        value: source,
      },
      mediaType: media("application/vnd.marimo.json.v1+json"),
      payload: source,
    });

    expect(result).toEqual(source);
    expect(result).not.toBe(source);
    expect(Object.isFrozen(result)).toBe(true);
    expect(Object.isFrozen((result as { readonly rows: readonly unknown[] }).rows)).toBe(true);
  });

  test("decodes UTF-8 text and keeps HTML selection disjoint", () => {
    const plain = blobInput("hello", "text/plain; charset=utf-8");
    const html = blobInput("<p>Ready</p>", "text/html");

    expect(textLoader().load(plain)).toBe("hello");
    expect(textLoader().accepts(plain.descriptor, plain.mediaType)).toBe(true);
    expect(textLoader().accepts(html.descriptor, html.mediaType)).toBe(false);
    expect(htmlLoader().accepts(html.descriptor, html.mediaType)).toBe(true);
    expect(htmlLoader().load(html)).toBe("<p>Ready</p>");
  });

  test("rejects incompatible text encodings and malformed UTF-8", () => {
    const latin1 = blobInput("hello", "text/plain; charset=iso-8859-1");
    const malformed = blobInput(new Uint8Array([0xc3, 0x28]), "text/plain");

    expect(textLoader().accepts(latin1.descriptor, latin1.mediaType)).toBe(false);
    expect(() => textLoader().load(malformed)).toThrow();
  });

  test("decodes canonical Marimo output and cell snapshots", async () => {
    const fixture = JSON.parse(await readFile(projectionFixturePath, "utf8")) as {
      readonly cell: JsonValue;
      readonly output: JsonValue;
    };
    const output = await marimoOutputLoader().load(
      snapshotInput("marimo.output.v1", fixture.output),
    );
    const cell = await marimoCellLoader().load(snapshotInput("marimo.cell.v1", fixture.cell));

    expect(output.schema).toBe("marimo.output.v1");
    expect(output.ownerCellId).toBe("cell-summary");
    expect(cell.schema).toBe("marimo.cell.v1");
    expect(cell.cell.name).toBe("summary");
  });
});

const blobInput = (value: string | Uint8Array, mediaType: string): BlobAssetLoadInput => {
  const parsed = media(mediaType);
  const data = typeof value === "string" ? encoder.encode(value) : value;
  return {
    descriptor: {
      asset: { sha256: "a".repeat(64), size: data.byteLength },
      codec: "marimo.blob-asset.msgpack.v1",
      filename: null,
      mediaType: parsed.raw,
      metadata: {},
      provenance: { pythonType: "marimo._save.cache.BlobAsset" },
    },
    mediaType: parsed,
    payload: { data, mediaType: parsed, filename: null, metadata: {} },
  };
};

const snapshotInput = <C extends "marimo.cell.v1" | "marimo.output.v1">(
  codec: C,
  value: JsonValue,
) => {
  const payload = encoder.encode(canonicalJson(value));
  const essence = `application/vnd.${codec}+json`;
  return {
    descriptor: {
      asset: { sha256: "a".repeat(64), size: payload.byteLength },
      codec,
      mediaType: essence,
      provenance: { pythonType: "marimo._save.stubs.lazy_stub.BlobAsset" },
    } as Extract<OutputDescriptor, { readonly codec: C }>,
    mediaType: media(essence),
    payload,
  };
};

const media = (raw: string): MediaType => {
  const [essence, ...parameters] = raw.split(";").map((part) => part.trim());
  const [type, subtype] = essence!.split("/") as [string, string];
  return {
    raw,
    essence: essence!,
    type,
    subtype,
    parameters: new Map(
      parameters.map((parameter) => {
        const [name, value] = parameter.split("=", 2);
        return [name!.toLowerCase(), value!] as const;
      }),
    ),
  };
};
