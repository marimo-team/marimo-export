import type { BlobAssetLoader, MediaType } from "../types.js";

const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

export const decodeUtf8Blob = ({
  payload,
  signal,
}: Parameters<BlobAssetLoader<string>["load"]>[0]): string => {
  signal?.throwIfAborted();
  const value = decoder.decode(payload.data);
  signal?.throwIfAborted();
  return value;
};

export const hasUtf8Charset = (mediaType: MediaType): boolean => {
  const charset = mediaType.parameters.get("charset");
  return (
    charset === undefined || charset.toLowerCase() === "utf-8" || charset.toLowerCase() === "utf8"
  );
};
