import type { AnyModel } from "@anywidget/types";

import { parseAnyWidgetPayload, readonlyModelState } from "./payload.js";
import type { ModelState } from "./runtime/model.js";
import { mountSnapshot } from "./runtime/registry.js";

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

export type AnyWidgetStateShape<State> = Partial<Record<keyof State, unknown>>;

export interface AnyWidgetMountOptions {
  readonly signal?: AbortSignal;
}

export interface MountedAnyWidget<
  State extends AnyWidgetStateShape<State> = ModelState,
  Exports = unknown,
> {
  readonly model: AnyModel<State>;
  readonly exports: Exports;
  dispose(): Promise<void>;
}

export interface LoadedAnyWidget<
  State extends AnyWidgetStateShape<State> = ModelState,
  Exports = unknown,
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
  Exports = unknown,
>(data: Uint8Array, signal?: AbortSignal): Promise<LoadedAnyWidget<State, Exports>> {
  signal?.throwIfAborted();
  const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(data));
  signal?.throwIfAborted();
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
