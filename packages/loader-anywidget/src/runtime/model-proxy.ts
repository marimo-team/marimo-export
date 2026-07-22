import type { AnyModel } from "@anywidget/types";

import type { ModelState } from "./model.js";

type EventHandler = (...args: unknown[]) => void;

export function modelProxy<T extends ModelState>(
  model: AnyModel<T>,
  signal: AbortSignal,
): AnyModel<T> {
  const events = model as unknown as {
    on(name: string, callback: EventHandler): void;
    off(name?: string | null, callback?: EventHandler | null): void;
  };
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
      events.on(name, callback);
      signal.addEventListener("abort", () => events.off(name, callback), { once: true });
    },
    off(name?: string | null, callback?: EventHandler | null): void {
      events.off(name ?? null, callback ?? null);
    },
    widget_manager: {
      async get_model<State extends ModelState>(modelId: string) {
        const child = await model.widget_manager.get_model<State>(modelId);
        return modelProxy(child, signal);
      },
    },
  };
  return proxy as AnyModel<T>;
}
