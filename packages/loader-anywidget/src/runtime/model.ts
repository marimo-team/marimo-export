import type { AnyModel } from "@anywidget/types";

import { isCallableValue } from "./value-types.js";

export type ModelValue =
  | null
  | boolean
  | number
  | string
  | DataView
  | readonly ModelValue[]
  | ModelState
  | undefined;

export interface ModelState {
  [key: string]: ModelValue;
}
export type ModelShape<State> = Partial<{ [Key in keyof State]: ModelValue }>;
type EventHandler = (...args: never[]) => void;

export interface ModelResolver {
  getModel(modelId: string): Promise<AnyModel<ModelState>>;
}

export class StaticModel<T extends ModelShape<T> = ModelState> implements AnyModel<T> {
  readonly #resolver: ModelResolver;
  readonly #dirtyFields = new Map<keyof T, ModelValue>();
  #data: T;
  #listeners = new Map<string, Set<EventHandler>>();
  #changeQueued = false;

  constructor(data: T, resolver: ModelResolver, signal: AbortSignal) {
    this.#data = data;
    this.#resolver = resolver;
    signal.addEventListener(
      "abort",
      () => {
        this.#listeners.clear();
        this.#dirtyFields.clear();
      },
      { once: true },
    );
  }

  get<K extends keyof T>(key: K): T[K] {
    if (Object.hasOwn(this.#data, key)) return this.#data[key];
    return missingModelValue<T[K]>();
  }

  set<K extends keyof T>(key: K, value: T[K]): void {
    this.#data = { ...this.#data, [key]: value };
    this.#dirtyFields.set(key, value);
    this.#emit(`change:${String(key)}`, value);
    this.#queueAnyChange();
  }

  save_changes(): void {
    this.#dirtyFields.clear();
  }

  readonly send: AnyModel<T>["send"] = (_content, callbacks) => {
    if (isCallableValue(callbacks)) queueMicrotask(callbacks);
  };

  readonly widget_manager = {
    get_model: async <State extends ModelShape<State>>(
      modelId: string,
    ): Promise<AnyModel<State>> => {
      const model = await this.#resolver.getModel(widgetManagerModelId(modelId));
      // SAFETY: The AnyWidget caller owns the requested state specialization for this model id.
      return model as AnyModel<State>;
    },
  };

  on(eventName: "msg:custom", callback: (msg: ModelValue, buffers: DataView[]) => void): void;
  on(eventName: `change:${string}`, callback: () => void): void;
  on(eventName: string, callback: EventHandler, options?: { signal?: AbortSignal }): void;
  on(eventName: string, callback: EventHandler, options?: { signal?: AbortSignal }): void {
    if (options?.signal?.aborted === true) return;
    const listeners = this.#listeners.get(eventName) ?? new Set<EventHandler>();
    listeners.add(callback);
    this.#listeners.set(eventName, listeners);
    options?.signal?.addEventListener("abort", () => this.off(eventName, callback), {
      once: true,
    });
  }

  off(eventName?: string | null, callback?: EventHandler | null): void {
    if (!eventName) {
      this.#listeners.clear();
      return;
    }
    if (!callback) {
      this.#listeners.delete(eventName);
      return;
    }
    this.#listeners.get(eventName)?.delete(callback);
  }

  #emit(eventName: string, ...args: ModelValue[]): void {
    const listeners = this.#listeners.get(eventName);
    if (listeners === undefined) return;
    // A callback may unregister the next listener while this event is emitted.
    // oxlint-disable-next-line no-useless-spread
    for (const listener of [...listeners]) {
      try {
        // SAFETY: EventHandler intentionally erases the event-specific argument tuple.
        listener(...(args as never[]));
      } catch (error) {
        console.error(`AnyWidget model listener for ${JSON.stringify(eventName)} failed.`, error);
      }
    }
  }

  #queueAnyChange(): void {
    if (this.#changeQueued) return;
    this.#changeQueued = true;
    queueMicrotask(() => {
      this.#changeQueued = false;
      this.#emit("change");
    });
  }
}

function missingModelValue<Value>(): Value {
  // SAFETY: AnyModel.get uses undefined for a key absent from the model state.
  return undefined as never;
}

function widgetManagerModelId(value: string): string {
  for (const prefix of ["IPY_MODEL_", "anywidget:"]) {
    if (value.startsWith(prefix) && value.length > prefix.length) return value.slice(prefix.length);
  }
  return value;
}
