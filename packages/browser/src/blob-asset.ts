import {
  canonicalJson,
  containsControlCharacter,
  containsUnpairedSurrogate,
  hasPythonBoundaryWhitespace,
  isPortablePathComponent,
  nonEmptyString,
  parseJsonObject,
} from "./schema.js";
import type { JsonObject } from "./types.js";
import { PublicationError } from "./types.js";
import type { ManifestFormat } from "./schema.js";
import { parseStrictJson, trimJsonWhitespace } from "./strict-json.js";
import { decodeBlobAssetWire } from "./strict-msgpack.js";

export interface DecodedBlobAsset {
  readonly data: Uint8Array;
  readonly mediaType: string;
  readonly filename: string | null;
  readonly formatId: string;
  readonly metadata: JsonObject;
}

export function decodeBlobAsset(bytes: Uint8Array, expected: ManifestFormat): DecodedBlobAsset {
  let asset;
  try {
    asset = decodeBlobAssetWire(bytes);
  } catch (error) {
    throw new PublicationError("asset_invalid", "Cache asset is not valid MessagePack.", {
      cause: error,
    });
  }

  try {
    const mediaType = nonEmptyString(asset.mediaType, "BlobAsset.media_type");
    const filename = parseFilename(asset.filename);
    const formatId = nonEmptyString(asset.formatId, "BlobAsset.metadata.format_id");
    const metadata = parseMetadata(asset.metadataJson);

    if (mediaType !== expected.media_type) {
      throw invalid("BlobAsset.media_type does not match the publication index.");
    }
    if (formatId !== expected.format_id) {
      throw invalid("BlobAsset.metadata.format_id does not match the publication index.");
    }
    if (canonicalJson(metadata) !== canonicalJson(expected.metadata)) {
      throw invalid("BlobAsset metadata does not match the publication index.");
    }

    return Object.freeze({
      data: asset.data,
      mediaType,
      filename,
      formatId,
      metadata,
    });
  } catch (error) {
    if (error instanceof PublicationError && error.code === "asset_invalid") throw error;
    if (error instanceof PublicationError) {
      throw new PublicationError("asset_invalid", error.message, { cause: error });
    }
    throw new PublicationError("asset_invalid", "Cache asset validation failed.", {
      cause: error,
    });
  }
}

function parseMetadata(bytes: Uint8Array): JsonObject {
  let decoded: unknown;
  try {
    const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(
      trimJsonWhitespace(bytes),
    );
    decoded = parseStrictJson(text);
  } catch (error) {
    throw new PublicationError("asset_invalid", "BlobAsset metadata must be strict UTF-8 JSON.", {
      cause: error,
    });
  }
  return parseJsonObject(decoded, "BlobAsset.metadata.metadata_json");
}

function parseFilename(value: unknown): string | null {
  if (value === null) return null;
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    hasPythonBoundaryWhitespace(value) ||
    !isPortablePathComponent(value) ||
    containsUnpairedSurrogate(value) ||
    containsControlCharacter(value)
  ) {
    throw invalid("BlobAsset.filename must be null or a portable base name.");
  }
  return value;
}

function invalid(message: string): PublicationError {
  return new PublicationError("asset_invalid", message);
}
