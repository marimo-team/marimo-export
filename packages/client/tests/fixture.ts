import { sha256Hex } from "../src/hash.js";
import type { ExportManifest, ManifestProjection } from "../src/schema.js";
import type { ExportRef, JsonObject } from "../src/types.js";

const encoder = new TextEncoder();

export interface ExportFixture {
  readonly index: ExportManifest;
  readonly ref: ExportRef;
  readonly indexBytes: Uint8Array;
  readonly objects: Record<string, Uint8Array>;
  readonly jsonPayload: Uint8Array;
  readonly textPayload: Uint8Array;
}

export async function exportFixture(): Promise<ExportFixture> {
  const jsonPayload = encoder.encode(JSON.stringify([{ symbol: "MSFT", price: 420 }]));
  const textPayload = Uint8Array.from([0xef, 0xbb, 0xbf, ...encoder.encode("MSFT: 420")]);
  const emptyPayload = new Uint8Array();
  const json = await projection("json.v1", "application/json", { rows: 1 }, jsonPayload);
  const text = await projection("text.v1", "text/plain", {}, textPayload);
  const empty = await projection("text.v1", "text/plain", {}, emptyPayload);
  const microsoftInputs = { symbol: "MSFT", window: 30 } satisfies JsonObject;
  const appleInputs = { symbol: "AAPL", window: 30 } satisfies JsonObject;
  const index: ExportManifest = {
    schema: "marimo-export.index.v1",
    notebook: { name: "finance.py", source_sha256: "b".repeat(64) },
    plan_sha256: "c".repeat(64),
    producer: {
      marimo_version: "0.23.14",
      marimo_export_version: "0.0.0",
    },
    scenarios: [
      {
        id: "microsoft",
        inputs: microsoftInputs,
        outputs: {
          prices: { json, text },
          empty: { text: empty },
        },
      },
      {
        id: "apple",
        inputs: appleInputs,
        outputs: {
          prices: { json },
        },
      },
    ],
  };
  const indexBytes = encoder.encode(JSON.stringify(index));
  const indexSha256 = await sha256Hex(indexBytes);
  const ref: ExportRef = Object.freeze({
    key: `marimo-export/indexes/${indexSha256}.json`,
    sha256: indexSha256,
    size: indexBytes.byteLength,
  });
  return {
    index,
    ref,
    indexBytes,
    objects: {
      "index.json": indexBytes,
      [`cache/${json.payload.key}`]: jsonPayload,
      [`cache/${text.payload.key}`]: textPayload,
      [`cache/${empty.payload.key}`]: emptyPayload,
    },
    jsonPayload,
    textPayload,
  };
}

async function projection(
  formatId: string,
  mediaType: string,
  metadata: JsonObject,
  payload: Uint8Array,
): Promise<ManifestProjection> {
  const digest = await sha256Hex(payload);
  return {
    format_id: formatId,
    media_type: mediaType,
    metadata,
    payload: {
      key: `marimo-export/payloads/sha256/${digest}`,
      sha256: digest,
      size: payload.byteLength,
    },
  };
}
