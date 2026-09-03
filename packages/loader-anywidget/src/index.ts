import type { AnyModel } from "@anywidget/types";

import { parseAnyWidgetPayload, readonlyModelState } from "./payload.js";
import type { ModelShape, ModelState } from "./runtime/model.js";
import { mountSnapshot } from "./runtime/registry.js";
import type { AnyWidgetMountOptions } from "./runtime/registry.js";

export {
  PreparedWidgetGraph,
  PreparedWidgetGraphReplacementError,
} from "./runtime/prepared-graph.js";
export type {
  PreparedWidgetGraphCheckpoint,
  PreparedWidgetGraphPort,
  PreparedWidgetGraphReplacement,
  PreparedWidgetGraphSnapshot,
} from "./runtime/prepared-graph.js";

export type AnyWidgetStateShape<State> = ModelShape<State>;

export interface MountedAnyWidget<
  State extends AnyWidgetStateShape<State> = ModelState,
  Exports extends object | undefined = object | undefined,
> {
  readonly model: AnyModel<State>;
  readonly exports: Exports;
  dispose(): Promise<void>;
}

export interface LoadedAnyWidget<
  State extends AnyWidgetStateShape<State> = ModelState,
  Exports extends object | undefined = object | undefined,
> {
  readonly initialState: Readonly<State>;
  mount(
    element: HTMLElement,
    options?: AnyWidgetMountOptions,
  ): Promise<MountedAnyWidget<State, Exports>>;
}

/** Decode exported AnyWidget bytes and prepare them for browser mounting. */
export async function loadAnyWidget<
  State extends AnyWidgetStateShape<State> = ModelState,
  Exports extends object | undefined = object | undefined,
>(data: Uint8Array, signal?: AbortSignal): Promise<LoadedAnyWidget<State, Exports>> {
  signal?.throwIfAborted();
  const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(data));
  signal?.throwIfAborted();
  const snapshot = parseAnyWidgetPayload(value);
  const root = snapshot.models.get(snapshot.rootModelId)!;
  const initialState = specializeState<State>(readonlyModelState(root.state));
  return Object.freeze({
    initialState,
    async mount(
      element: HTMLElement,
      options: AnyWidgetMountOptions = {},
    ): Promise<MountedAnyWidget<State, Exports>> {
      return mountSnapshot<State, Exports>(snapshot, element, options);
    },
  });
}

export type { AnyModel, AnyWidgetMountOptions, ModelState };

function specializeState<State extends AnyWidgetStateShape<State>>(
  state: Readonly<ModelState>,
): Readonly<State> {
  // SAFETY: The caller supplies the AnyWidget state specialization for the parsed root model.
  return state as Readonly<State>;
}
