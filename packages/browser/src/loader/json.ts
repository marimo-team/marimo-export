import { portableJsonValue } from "@marimo-team/portable-json";
import type { JsonValue } from "@marimo-team/portable-json";

import { defineOutputLoader } from "../loader.js";
import type { OutputLoader } from "../types.js";

/** Return one detached, immutable JSON projection. */
export const jsonLoader = (): OutputLoader<"marimo.json.v1", JsonValue> =>
  defineOutputLoader({
    codec: "marimo.json.v1",
    accepts: (_descriptor, mediaType) =>
      mediaType.essence === "application/vnd.marimo.json.v1+json",
    load: ({ payload, signal }) => {
      signal?.throwIfAborted();
      const value = portableJsonValue(payload);
      signal?.throwIfAborted();
      return value;
    },
  });
