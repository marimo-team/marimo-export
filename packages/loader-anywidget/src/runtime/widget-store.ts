import type { ClientHost, StandaloneWidget } from "#anywidget/runtime/types";

export type WidgetStoreSource<T extends object> = ClientHost<T> | StandaloneWidget<T>;

export type CreateWidgetStoreOptions = {
  commit?: boolean;
};

export type WidgetStoreWriteOptions = {
  commit?: boolean;
};

export type WidgetStoreSelectOptions<S> = {
  equals?: (a: S, b: S) => boolean;
  fireImmediately?: boolean;
};

export type WidgetStore<T extends object> = {
  get(): Readonly<T>;
  getSnapshot(): Readonly<T>;
  subscribe(listener: () => void): () => void;
  select<S>(
    selector: (state: Readonly<T>) => S,
    listener: (value: S, previous: S, state: Readonly<T>) => void,
    options?: WidgetStoreSelectOptions<S>,
  ): () => void;
  set<K extends keyof T>(key: K, value: T[K], options?: WidgetStoreWriteOptions): void;
  patch(
    patch: Partial<T> | ((state: Readonly<T>) => Partial<T>),
    options?: WidgetStoreWriteOptions,
  ): void;
  replace(next: T | ((state: Readonly<T>) => T), options?: WidgetStoreWriteOptions): void;
  commit(): void;
};

function resolveHost<T extends object>(source: WidgetStoreSource<T>): ClientHost<T> {
  if ("host" in source) {
    return source.host;
  }
  return source;
}

export function createWidgetStore<T extends object>(
  source: WidgetStoreSource<T>,
  options: CreateWidgetStoreOptions = {},
): WidgetStore<T> {
  const host = resolveHost(source);
  const commitByDefault = options.commit ?? true;
  const listeners = new Set<() => void>();
  let snapshot = host.getState();

  host.onStateChange((_patch, fullState) => {
    snapshot = fullState;
    const currentListeners = Array.from(listeners);
    for (const listener of currentListeners) {
      listener();
    }
  });

  const shouldCommit = (writeOptions?: WidgetStoreWriteOptions): boolean =>
    writeOptions?.commit ?? commitByDefault;

  const store: WidgetStore<T> = {
    get() {
      return snapshot;
    },
    getSnapshot() {
      return snapshot;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    select(selector, listener, selectOptions) {
      const equals = selectOptions?.equals ?? Object.is;
      let previous = selector(snapshot);

      if (selectOptions?.fireImmediately ?? true) {
        listener(previous, previous, snapshot);
      }

      return store.subscribe(() => {
        const state = store.get();
        const next = selector(state);
        if (equals(next, previous)) {
          return;
        }

        const last = previous;
        previous = next;
        listener(next, last, state);
      });
    },
    set(key, value, writeOptions) {
      host.setState({ [key]: value } as unknown as Partial<T>, {
        commit: shouldCommit(writeOptions),
      });
    },
    patch(patch, writeOptions) {
      const nextPatch = typeof patch === "function" ? patch(snapshot) : patch;
      host.setState(nextPatch, { commit: shouldCommit(writeOptions) });
    },
    replace(next, writeOptions) {
      const nextState = typeof next === "function" ? next(snapshot) : next;
      host.replaceState(nextState, { commit: shouldCommit(writeOptions) });
    },
    commit() {
      host.commit();
    },
  };

  return store;
}
