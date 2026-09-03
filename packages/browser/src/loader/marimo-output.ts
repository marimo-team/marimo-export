import { defineOutputLoader } from "../loader.js";
import { parseMarimoOutputSnapshot } from "../marimo-snapshot.js";
import type { MarimoOutputSnapshot } from "../marimo-snapshot.js";
import type { OutputLoader } from "../types.js";

/** Decode one inert Marimo output snapshot. */
export const marimoOutputLoader = (): OutputLoader<"marimo.output.v1", MarimoOutputSnapshot> =>
  defineOutputLoader({
    codec: "marimo.output.v1",
    accepts: (_descriptor, mediaType) =>
      mediaType.essence === "application/vnd.marimo.output.v1+json",
    load: ({ payload, signal }) => {
      signal?.throwIfAborted();
      const snapshot = parseMarimoOutputSnapshot(payload);
      signal?.throwIfAborted();
      return snapshot;
    },
  });
