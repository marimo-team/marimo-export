import type { AnyModel } from "@anywidget/types";
import { defineBlobAssetLoader } from "@marimo-team/marimo-export";
import type { BlobAssetLoadInput, BlobAssetLoader } from "@marimo-team/marimo-export";

import { parseAnyWidgetPayload, readonlyModelState } from "./payload.js";
import { mountSnapshot } from "./runtime/registry.js";
import type { ModelState } from "./runtime/model.js";

const MEDIA_TYPE = "application/vnd.marimo-export.anywidget.v1+json";
type StateShape<State> = Partial<Record<keyof State, unknown>>;

export interface AnyWidgetMountOptions {
  readonly signal?: AbortSignal;
}

export interface MountedAnyWidget<State extends StateShape<State> = ModelState, Exports = unknown> {
  readonly model: AnyModel<State>;
  readonly exports: Exports;
  dispose(): Promise<void>;
}

export interface LoadedAnyWidget<State extends StateShape<State> = ModelState, Exports = unknown> {
  readonly initialState: Readonly<State>;
  mount(
    element: HTMLElement,
    options?: AnyWidgetMountOptions,
  ): Promise<MountedAnyWidget<State, Exports>>;
}

/** Decode a static AnyWidget projection and prepare it for browser mounting. */
export function anyWidgetLoader<
  State extends StateShape<State> = ModelState,
  Exports = unknown,
>(): BlobAssetLoader<LoadedAnyWidget<State, Exports>> {
  return defineBlobAssetLoader({
    mediaTypes: MEDIA_TYPE,
    load(input) {
      return loadWidget<State, Exports>(input);
    },
  });
}

async function loadWidget<State extends StateShape<State>, Exports>(
  input: BlobAssetLoadInput,
): Promise<LoadedAnyWidget<State, Exports>> {
  input.signal?.throwIfAborted();
  const value: unknown = JSON.parse(
    new TextDecoder("utf-8", { fatal: true }).decode(input.payload.data),
  );
  input.signal?.throwIfAborted();
  const snapshot = parseAnyWidgetPayload(value);
  const root = snapshot.models.get(snapshot.rootModelId)!;
  const initialState = readonlyModelState(root.state) as Readonly<State>;
  return Object.freeze({
    initialState,
    async mount(
      element: HTMLElement,
      options: AnyWidgetMountOptions = {},
    ): Promise<MountedAnyWidget<State, Exports>> {
      return (await mountSnapshot<ModelState, Exports>(
        snapshot,
        element,
        options,
      )) as unknown as MountedAnyWidget<State, Exports>;
    },
  });
}

export type { AnyModel };
