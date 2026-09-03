import { loadAnyWidget } from "@marimo-export/internal-loader-anywidget";
import type {
  AnyWidgetStateShape,
  LoadedAnyWidget,
  ModelState,
} from "@marimo-export/internal-loader-anywidget";

import { defineBlobAssetLoader } from "../loader.js";
import type { BlobAssetLoader } from "../types.js";

export {
  PreparedWidgetGraph,
  PreparedWidgetGraphReplacementError,
} from "@marimo-export/internal-loader-anywidget";
export type {
  AnyModel,
  AnyWidgetMountOptions,
  AnyWidgetStateShape,
  LoadedAnyWidget,
  ModelState,
  MountedAnyWidget,
  PreparedWidgetGraphCheckpoint,
  PreparedWidgetGraphPort,
  PreparedWidgetGraphReplacement,
  PreparedWidgetGraphSnapshot,
} from "@marimo-export/internal-loader-anywidget";

const MEDIA_TYPE = "application/vnd.marimo-export.anywidget.v1+json";

/** Decode an exported AnyWidget value and prepare it for browser mounting. */
export const anyWidgetLoader = <
  State extends AnyWidgetStateShape<State> = ModelState,
  Exports extends object | undefined = object | undefined,
>(): BlobAssetLoader<LoadedAnyWidget<State, Exports>> =>
  defineBlobAssetLoader({
    mediaTypes: MEDIA_TYPE,
    load: ({ payload, signal }) => loadAnyWidget<State, Exports>(payload.data, signal),
  });
