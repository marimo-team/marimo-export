import { encode } from "@msgpack/msgpack";

import { canonicalJson } from "../src/schema.js";

const encoder = new TextEncoder();

export interface Fixture {
  readonly index: Record<string, unknown>;
  readonly indexBytes: Uint8Array;
  readonly assets: ReadonlyMap<string, Uint8Array>;
  readonly requests: string[];
  readonly fetch: typeof globalThis.fetch;
}

export async function exportFixture(
  options: {
    readonly envelope?: Uint8Array;
    readonly blobMetadata?: Record<string, unknown>;
    readonly blobMediaType?: string;
    readonly blobFilename?: string | null;
    readonly indexTransform?: (index: Record<string, unknown>) => void;
  } = {},
): Promise<Fixture> {
  const blobMetadata = options.blobMetadata ?? { representation: "fixture" };
  const blobMediaType = options.blobMediaType ?? "application/vnd.example.fixture+json";
  const blobFilename = options.blobFilename === undefined ? "fixture.json" : options.blobFilename;
  const envelope =
    options.envelope ??
    encode({
      data: encoder.encode('{"ready":true}'),
      media_type: blobMediaType,
      filename: blobFilename,
      metadata: blobMetadata,
    });
  const npy = new Uint8Array([0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59, 0x01, 0x00]);
  const arrow = encoder.encode("ARROW1ARROW1");
  const blobDigest = await digest(envelope);
  const npyDigest = await digest(npy);
  const arrowDigest = await digest(arrow);
  const inputs = ["symbol", "width"];
  const outputs = ["count", "array", "table", "view"];
  const definitions = [
    ["zeta", { symbol: "MSFT", width: 640 }, 2],
    ["alpha", { symbol: "AAPL", width: 800 }, 1],
  ] as const;
  const fingerprints = await Promise.all(
    definitions.map(([, vector]) => digest(encoder.encode(canonicalJson(vector)))),
  );
  const states: Record<string, unknown> = {};
  definitions.forEach(([name, vector, count], index) => {
    states[name] = {
      fingerprint: fingerprints[index]!,
      inputs: vector,
      outputs: {
        count: scalar(count),
        array: assetDescriptor("numpy.npy.v1", "application/x-npy", npyDigest, npy.byteLength),
        table: assetDescriptor(
          "apache.arrow.file.v1",
          "application/vnd.apache.arrow.file",
          arrowDigest,
          arrow.byteLength,
        ),
        view: {
          asset: { sha256: blobDigest, size: envelope.byteLength },
          codec: "marimo.blob-asset.msgpack.v1",
          filename: blobFilename,
          media_type: blobMediaType,
          metadata: blobMetadata,
          provenance: provenance("view", true),
        },
      },
    };
  });
  const index: Record<string, unknown> = {
    inputs,
    notebook: { document_sha256: "a".repeat(64), filename: "finance.py" },
    outputs,
    producer: { marimo: "0.23.15", marimo_export: "1.0.0" },
    schema: "marimo-export.export.v1",
    states,
  };
  options.indexTransform?.(index);
  const indexBytes = encoder.encode(canonicalJson(index as never));
  const assets = new Map([
    [`assets/${npyDigest}.npy`, npy],
    [`assets/${arrowDigest}.arrow`, arrow],
    [`assets/${blobDigest}.bin`, envelope],
  ]);
  const requests: string[] = [];
  const fetch: typeof globalThis.fetch = async (input, init) => {
    if (init?.signal?.aborted) throw init.signal.reason;
    const url = input instanceof Request ? input.url : input.toString();
    requests.push(url);
    const path = new URL(url).pathname.split("/stocks/")[1];
    if (path === "index.json") return new Response(indexBytes);
    const value = path === undefined ? undefined : assets.get(path);
    return value === undefined ? new Response(null, { status: 404 }) : new Response(value.slice());
  };
  return { index, indexBytes, assets, requests, fetch };
}

export function scalar(value: unknown): Record<string, unknown> {
  return {
    codec: "marimo.scalar.v1",
    media_type: "application/vnd.marimo.scalar.v1+json",
    provenance: provenance("count", false),
    value,
  };
}

function assetDescriptor(
  codec: string,
  mediaType: string,
  sha256: string,
  size: number,
): Record<string, unknown> {
  return {
    asset: { sha256, size },
    codec,
    media_type: mediaType,
    provenance: provenance(codec, true),
  };
}

function provenance(name: string, asset: boolean): Record<string, unknown> {
  return {
    cache_key: `cell_cache/O_${name}.json`,
    python_type: "fixture.Value",
    return_reference: asset ? `cell_cache/${name}/return.bin` : null,
  };
}

export async function digest(bytes: Uint8Array): Promise<string> {
  const value = await crypto.subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>);
  return [...new Uint8Array(value)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function hexBytes(value: string): Uint8Array {
  return Uint8Array.from(value.match(/../gu)!.map((byte) => Number.parseInt(byte, 16)));
}
