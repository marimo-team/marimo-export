import type { AnyModel } from "@anywidget/types";

import type { ModelShape, ModelValue } from "./model.js";

type EventHandler = (...args: ModelValue[]) => void;

interface ModelEvents {
  on(name: string, callback: EventHandler): void;
  off(name?: string | null, callback?: EventHandler | null): void;
}

interface ListenerRegistration {
  readonly name: string;
  readonly callback: EventHandler;
  readonly onAbort: () => void;
}

export function modelProxy<T extends ModelShape<T>>(
  model: AnyModel<T>,
  signal: AbortSignal,
): AnyModel<T> {
  const events = modelEvents(model);
  const listeners = new Set<ListenerRegistration>();
  const proxy = {
    get<K extends keyof T>(key: K): T[K] {
      return model.get(key);
    },
    set<K extends keyof T>(key: K, value: T[K]): void {
      model.set(key, value);
    },
    save_changes() {
      model.save_changes();
    },
    send: model.send.bind(model),
    on(name: string, callback: EventHandler): void {
      if (signal.aborted) return;
      let registration: ListenerRegistration;
      const onAbort = () => {
        listeners.delete(registration);
        events.off(name, callback);
      };
      registration = { name, callback, onAbort };
      listeners.add(registration);
      signal.addEventListener("abort", onAbort, { once: true });
      try {
        events.on(name, callback);
      } catch (error) {
        signal.removeEventListener("abort", onAbort);
        listeners.delete(registration);
        throw error;
      }
    },
    off(name?: string | null, callback?: EventHandler | null): void {
      const normalizedName = name ?? null;
      const normalizedCallback = callback ?? null;
      try {
        events.off(normalizedName, normalizedCallback);
      } finally {
        for (const listener of listeners) {
          const matchesName = normalizedName === null || listener.name === normalizedName;
          const matchesCallback =
            normalizedCallback === null || listener.callback === normalizedCallback;
          if (!matchesName || !matchesCallback) continue;
          signal.removeEventListener("abort", listener.onAbort);
          listeners.delete(listener);
        }
      }
    },
    widget_manager: {
      async get_model<State extends ModelShape<State>>(modelId: string) {
        const child = await model.widget_manager.get_model<State>(modelId);
        return modelProxy(child, signal);
      },
    },
  };
  // SAFETY: The proxy delegates every AnyModel member and preserves the supplied state type.
  return proxy as AnyModel<T>;
}

function modelEvents<T extends ModelShape<T>>(model: AnyModel<T>): ModelEvents {
  // SAFETY: AnyModel exposes compatible on and off methods with broader upstream event arguments.
  return model as ModelEvents;
}
