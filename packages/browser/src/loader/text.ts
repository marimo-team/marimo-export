import { defineBlobAssetLoader } from "../loader.js";
import type { BlobAssetLoader } from "../types.js";
import { decodeUtf8Blob, hasUtf8Charset } from "./utf8.js";

/** Decode a UTF-8 text BlobAsset. */
export const textLoader = (): BlobAssetLoader<string> =>
  defineBlobAssetLoader({
    mediaTypes: (mediaType) =>
      mediaType.type === "text" && mediaType.essence !== "text/html" && hasUtf8Charset(mediaType),
    load: decodeUtf8Blob,
  });
