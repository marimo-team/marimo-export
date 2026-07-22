import type { AnyModel } from "@anywidget/types";

export type ModelState = Record<string, unknown>;
type EventHandler = (...args: never[]) => void;

export interface ModelResolver {
  getModel(modelId: string): Promise<AnyModel<ModelState>>;
}

export class StaticModel<T extends ModelState = ModelState> implements AnyModel<T> {
  readonly #resolver: ModelResolver;
  readonly #dirtyFields = new Map<keyof T, unknown>();
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
    return this.#data[key];
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

  send(_content: unknown, callbacks?: unknown, _buffers?: ArrayBuffer[] | ArrayBufferView[]): void {
    if (typeof callbacks === "function") queueMicrotask(callbacks as () => void);
  }

  readonly widget_manager = {
    get_model: async <State extends ModelState>(modelId: string): Promise<AnyModel<State>> =>
      (await this.#resolver.getModel(widgetManagerModelId(modelId))) as AnyModel<State>,
  };

  on(eventName: "msg:custom", callback: (msg: unknown, buffers: DataView[]) => void): void;
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

  #emit(eventName: string, ...args: unknown[]): void {
    const listeners = this.#listeners.get(eventName);
    if (listeners === undefined) return;
    // A callback may unregister the next listener while this event is emitted.
    // oxlint-disable-next-line no-useless-spread
    for (const listener of [...listeners]) {
      try {
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

function widgetManagerModelId(value: string): string {
  for (const prefix of ["IPY_MODEL_", "anywidget:"]) {
    if (value.startsWith(prefix) && value.length > prefix.length) return value.slice(prefix.length);
  }
  return value;
}
