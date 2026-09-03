import { defineBlobAssetLoader } from "../loader.js";
import type { BlobAssetLoader } from "../types.js";
import { decodeUtf8Blob, hasUtf8Charset } from "./utf8.js";

/** Decode a UTF-8 HTML BlobAsset into an inert string. */
export const htmlLoader = (): BlobAssetLoader<string> =>
  defineBlobAssetLoader({
    mediaTypes: (mediaType) => mediaType.essence === "text/html" && hasUtf8Charset(mediaType),
    load: decodeUtf8Blob,
  });
