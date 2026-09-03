import { decode } from "@msgpack/msgpack";
import { portableJsonObject } from "@marimo-team/portable-json";

import { parseMediaType } from "./media-type.js";
import { canonicalJson } from "./schema.js";
import { validateCanonicalMessagePack } from "./strict-msgpack.js";
import type { BlobAsset, BlobAssetDescriptor } from "./types.js";
import { isNotebookExportError, NotebookExportError } from "./types.js";
import { isRecordValue, isStringValue } from "./value-types.js";

const EXPECTED_FIELDS = ["data", "media_type", "filename", "metadata"];

export function decodeBlobAsset(bytes: Uint8Array, descriptor: BlobAssetDescriptor): BlobAsset {
  try {
    validateCanonicalMessagePack(bytes);
    const decoded = decode(bytes);
    if (!isRecordValue(decoded)) {
      throw new TypeError("BlobAsset must be a map.");
    }
    const fields = Object.keys(decoded);
    if (
      fields.length !== EXPECTED_FIELDS.length ||
      fields.some((field, index) => field !== EXPECTED_FIELDS[index])
    ) {
      throw new TypeError("BlobAsset must use the native four-field envelope.");
    }
    if (!("data" in decoded) || !(decoded.data instanceof Uint8Array)) {
      throw new TypeError("BlobAsset.data must be binary.");
    }
    if (!("media_type" in decoded) || !isStringValue(decoded.media_type)) {
      throw new TypeError("BlobAsset.media_type must be a string.");
    }
    const mediaType = parseMediaType(decoded.media_type);
    if (!("filename" in decoded)) throw new TypeError("BlobAsset.filename must be present.");
    const filename = decoded.filename;
    if (filename !== null && !isStringValue(filename)) {
      throw new TypeError("BlobAsset.filename must be a string or null.");
    }
    if (!("metadata" in decoded)) throw new TypeError("BlobAsset.metadata must be present.");
    const metadata = portableJsonObject(decoded.metadata, "BlobAsset.metadata");
    if (decoded.media_type !== descriptor.mediaType) {
      throw new TypeError("BlobAsset.media_type does not match its descriptor.");
    }
    if (filename !== descriptor.filename) {
      throw new TypeError("BlobAsset.filename does not match its descriptor.");
    }
    if (canonicalJson(metadata) !== canonicalJson(descriptor.metadata)) {
      throw new TypeError("BlobAsset.metadata does not match its descriptor.");
    }
    return Object.freeze({
      data: decoded.data,
      mediaType,
      filename,
      metadata,
    });
  } catch (error) {
    if (isNotebookExportError(error) && error.code === "asset_invalid") throw error;
    throw new NotebookExportError("asset_invalid", "BlobAsset envelope validation failed.", {
      cause: error,
      details: { outputCodec: descriptor.codec, mediaType: descriptor.mediaType },
    });
  }
}
