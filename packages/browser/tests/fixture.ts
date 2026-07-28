import { encode } from "@msgpack/msgpack";

import { openPublicationFromSource } from "../src/publication.js";
import { memorySource } from "../src/source.js";
import { canonicalJson } from "../src/schema.js";
import type { FormatLoader } from "../src/loader.js";
import type { JsonObject } from "../src/types.js";

const encoder = new TextEncoder();

export interface FixtureOptions {
  readonly data?: Uint8Array;
  readonly formatId?: string;
  readonly mediaType?: string;
  readonly metadata?: JsonObject;
  readonly filename?: string | null;
  readonly envelopeMetadata?: unknown;
  readonly envelopeMediaType?: string;
  readonly envelope?: Uint8Array;
  readonly assetKey?: string;
  readonly loaders?: readonly FormatLoader[];
}

export async function fixture(options: FixtureOptions = {}) {
  const data = options.data ?? encoder.encode('{"answer":42}');
  const formatId = options.formatId ?? "json.v1";
  const mediaType = options.mediaType ?? "application/json";
  const metadata = options.metadata ?? Object.freeze({});
  const envelope =
    options.envelope ??
    encode({
      data,
      media_type: options.envelopeMediaType ?? mediaType,
      filename: options.filename ?? null,
      metadata: options.envelopeMetadata ?? {
        format_id: formatId,
        metadata_json: encoder.encode(canonicalJson(metadata)),
      },
    });
  const sha256 = await digest(envelope);
  const assetKey = options.assetKey ?? "C_fixture/return.bin";
  const index = indexFor({
    formatId,
    mediaType,
    metadata,
    assetKey,
    sha256,
    size: envelope.byteLength,
  });
  const indexBytes = encoder.encode(JSON.stringify(index));
  const source = memorySource({ "index.json": indexBytes, [`cache/${assetKey}`]: envelope });
  const publication = await openPublicationFromSource(
    source,
    options.loaders === undefined ? {} : { loaders: options.loaders },
  );
  return { publication, index, indexBytes, envelope, source, assetKey };
}

export function indexFor(
  options: {
    readonly formatId?: string;
    readonly mediaType?: string;
    readonly metadata?: JsonObject;
    readonly assetKey?: string;
    readonly sha256?: string;
    readonly size?: number;
  } = {},
) {
  return {
    schema: "marimo-export.publication.v1",
    asset_codec: "marimo.blob-asset.msgpack.v1",
    notebook: { filename: "finance.py", document_sha256: "a".repeat(64) },
    producer: { marimo: "0.24.0", marimo_export: "0.0.0" },
    variants: {
      current: {
        controls: {},
        outputs: {
          summary: {
            formats: {
              json: {
                format_id: options.formatId ?? "json.v1",
                media_type: options.mediaType ?? "application/json",
                metadata: options.metadata ?? {},
                asset: {
                  key: options.assetKey ?? "C_fixture/return.bin",
                  sha256: options.sha256 ?? "b".repeat(64),
                  size: options.size ?? 1,
                },
              },
            },
          },
        },
      },
    },
  };
}

export async function digest(bytes: Uint8Array): Promise<string> {
  const value = await crypto.subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>);
  return [...new Uint8Array(value)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
