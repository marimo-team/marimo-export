import { defineOutputLoader } from "../loader.js";
import { parseMarimoCellSnapshot } from "../marimo-snapshot.js";
import type { MarimoCellSnapshot } from "../marimo-snapshot.js";
import type { OutputLoader } from "../types.js";

/** Decode one inert complete-cell snapshot. */
export const marimoCellLoader = (): OutputLoader<"marimo.cell.v1", MarimoCellSnapshot> =>
  defineOutputLoader({
    codec: "marimo.cell.v1",
    accepts: (_descriptor, mediaType) =>
      mediaType.essence === "application/vnd.marimo.cell.v1+json",
    load: ({ payload, signal }) => {
      signal?.throwIfAborted();
      const snapshot = parseMarimoCellSnapshot(payload);
      signal?.throwIfAborted();
      return snapshot;
    },
  });
