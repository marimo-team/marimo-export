import type { AnyModel } from "@anywidget/types";
import type { FormatLoader, FormatLoaderContext } from "@marimo-team/marimo-export";

import { parseAnyWidgetPayload, readonlyModelState } from "./payload.js";
import { mountSnapshot } from "./runtime/registry.js";
import type { ModelState } from "./runtime/model.js";

const FORMAT_ID = "anywidget.v1";
const MEDIA_TYPE = "application/vnd.marimo-export.anywidget+json";
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
>(): FormatLoader<LoadedAnyWidget<State, Exports>> {
  return {
    formatId: FORMAT_ID,
    load(output) {
      return loadWidget<State, Exports>(output);
    },
    async mount(output, element) {
      return (await loadWidget<State, Exports>(output)).mount(
        element,
        output.signal === undefined ? {} : { signal: output.signal },
      );
    },
  };
}

async function loadWidget<State extends StateShape<State>, Exports>(
  output: FormatLoaderContext,
): Promise<LoadedAnyWidget<State, Exports>> {
  if (output.mediaType !== MEDIA_TYPE) {
    throw new TypeError(`AnyWidget output media type must be ${JSON.stringify(MEDIA_TYPE)}.`);
  }
  const snapshot = parseAnyWidgetPayload(await output.json());
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
