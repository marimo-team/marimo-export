import { normalizeOutgoingBuffers, toDataViews } from "#anywidget/runtime/buffers";
import type {
  AnyModel,
  Cleanup,
  ClientCommand,
  ClientHost,
  CreateStandaloneWidgetOptions,
  StandaloneWidget,
  WidgetDefinition,
  WidgetModuleNamespace,
} from "#anywidget/runtime/types";

export { restoreBufferBytes, restoreBuffers } from "#anywidget/runtime/buffers";
export type { ClientCommand, ClientHost, StandaloneWidget } from "#anywidget/runtime/types";

const STYLE_IDS = new Set<string>();

function registerListener<T>(listeners: Set<T>, listener: T): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

type ChangeListener = () => void;
type CustomListener = (msg: unknown, buffers: DataView[]) => void;
type SaveListener<T extends object> = (patch: Partial<T>, fullState: T) => void;
type SendListener = (msg: unknown, buffers: Uint8Array[]) => void;

function ensureInlineCss(anywidgetId: string, cssText?: string | null): void {
  if (!cssText || STYLE_IDS.has(anywidgetId) || typeof document === "undefined") {
    return;
  }

  const style = document.createElement("style");
  style.id = anywidgetId;
  style.textContent = cssText;
  document.head.appendChild(style);
  STYLE_IDS.add(anywidgetId);
}

async function runCleanup(cleanup: Cleanup): Promise<void> {
  const resolved = await cleanup;
  if (typeof resolved === "function") {
    await resolved();
  }
}

async function resolveWidget<T extends object>(
  widgetModule: WidgetModuleNamespace<T>,
): Promise<WidgetDefinition<T>> {
  if (widgetModule.render) {
    return { render: widgetModule.render };
  }

  if (!widgetModule.default) {
    throw new Error("Standalone anywidget export requires a default widget export.");
  }

  const widget =
    typeof widgetModule.default === "function"
      ? await widgetModule.default()
      : widgetModule.default;
  return widget;
}

export async function createStandaloneWidget<T extends object>(
  options: CreateStandaloneWidgetOptions<T>,
): Promise<StandaloneWidget<T>> {
  ensureInlineCss(options.anywidgetId, options.inlineCssText);

  const state = { ...options.initialState, ...options.input?.state } as T;
  const dirtyPatch: Partial<T> = {};
  const commands = new Map<string, ClientCommand>(Object.entries(options.input?.commands ?? {}));
  const changeListeners = new Map<string, Set<ChangeListener>>();
  const customListeners = new Set<CustomListener>();
  const saveListeners = new Set<SaveListener<T>>();
  const sendListeners = new Set<SendListener>();
  const stateChangeListeners = new Set<SaveListener<T>>();
  const mounts = new Map<HTMLElement, { unmount(): Promise<void> }>();

  let destroyed = false;
  let initializeCleanup: Cleanup = undefined;

  const assertActive = (): void => {
    if (destroyed) {
      throw new Error("This standalone widget instance has already been destroyed.");
    }
  };

  const emitPatch = (patch: Partial<T>): void => {
    const changedKeys = Object.keys(patch) as Array<keyof T & string>;
    if (changedKeys.length === 0) {
      return;
    }

    for (const key of changedKeys) {
      for (const listener of changeListeners.get(`change:${key}`) ?? []) {
        listener();
      }
    }

    for (const listener of stateChangeListeners) {
      listener({ ...patch }, { ...state });
    }
  };

  const commitDirtyPatch = (): void => {
    const patch = { ...dirtyPatch };
    if (Object.keys(patch).length === 0) {
      return;
    }
    for (const key of Object.keys(dirtyPatch) as Array<keyof T>) {
      delete dirtyPatch[key];
    }
    for (const listener of saveListeners) {
      listener(patch, { ...state });
    }
  };

  const applyPatch = (patch: Partial<T>, commit = false): void => {
    assertActive();
    const changed: Partial<T> = {};
    for (const [rawKey, value] of Object.entries(patch) as Array<
      [keyof T & string, T[keyof T & string]]
    >) {
      if (Object.is(state[rawKey], value)) {
        continue;
      }
      state[rawKey] = value as T[keyof T & string];
      dirtyPatch[rawKey] = value as T[keyof T & string];
      changed[rawKey] = value as T[keyof T & string];
    }
    emitPatch(changed);
    if (commit) {
      commitDirtyPatch();
    }
  };

  const model: AnyModel<T> = {
    get<K extends keyof T>(key: K): T[K] {
      return state[key];
    },
    set<K extends keyof T>(key: K, value: T[K]): void {
      applyPatch({ [key]: value } as unknown as Partial<T>);
    },
    on(
      eventName: "msg:custom" | `change:${string}`,
      callback: CustomListener | ChangeListener,
    ): void {
      if (eventName === "msg:custom") {
        customListeners.add(callback as CustomListener);
        return;
      }
      const listeners = changeListeners.get(eventName) ?? new Set<ChangeListener>();
      listeners.add(callback as ChangeListener);
      changeListeners.set(eventName, listeners);
    },
    off(eventName?: string | null, callback?: ((...args: unknown[]) => void) | null): void {
      if (!eventName) {
        changeListeners.clear();
        customListeners.clear();
        return;
      }
      if (eventName === "msg:custom") {
        if (!callback) {
          customListeners.clear();
          return;
        }
        customListeners.delete(callback as CustomListener);
        return;
      }
      if (!callback) {
        changeListeners.delete(eventName);
        return;
      }
      changeListeners.get(eventName)?.delete(callback as ChangeListener);
    },
    save_changes(): void {
      commitDirtyPatch();
    },
    send(
      content: unknown,
      _callbacks?: unknown,
      buffers?: ArrayBuffer[] | ArrayBufferView[],
    ): void {
      const normalized = normalizeOutgoingBuffers(buffers);
      for (const listener of sendListeners) {
        listener(content, normalized);
      }
    },
    widget_manager: {
      async get_model(model_id: string) {
        throw new Error(
          `Standalone widget runtime does not support widget_manager.get_model(${JSON.stringify(model_id)}).`,
        );
      },
    },
  };

  const experimental = {
    async invoke<R>(
      name: string,
      msg?: unknown,
      invokeOptions?: { buffers?: DataView[]; signal?: AbortSignal },
    ): Promise<[R, DataView[]]> {
      const handler = commands.get(name);
      if (!handler) {
        throw new Error(`No client-side command is registered for ${JSON.stringify(name)}.`);
      }
      const inputBuffers = (invokeOptions?.buffers ?? []).map(
        (buffer) => new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength),
      );
      const handlerOptions: { buffers: Uint8Array[]; signal?: AbortSignal } = {
        buffers: inputBuffers,
      };
      if (invokeOptions?.signal !== undefined) {
        handlerOptions.signal = invokeOptions.signal;
      }
      const [response, buffers] = await handler(msg, handlerOptions);
      return [response as R, toDataViews(buffers)];
    },
  };

  const widget = await resolveWidget(options.widgetModule);
  initializeCleanup = await widget.initialize?.({ model, experimental });

  const host: ClientHost<T> = {
    getState() {
      return { ...state };
    },
    setState(patch: Partial<T>, hostOptions?: { commit?: boolean }): void {
      applyPatch(patch, hostOptions?.commit ?? false);
    },
    replaceState(next: T, hostOptions?: { commit?: boolean }): void {
      assertActive();
      const changed: Partial<T> = {};
      const nextKeys = new Set<keyof T & string>([
        ...(Object.keys(state) as Array<keyof T & string>),
        ...(Object.keys(next) as Array<keyof T & string>),
      ]);
      for (const key of nextKeys) {
        const value = next[key];
        if (Object.is(state[key], value)) {
          continue;
        }
        state[key] = value as T[keyof T & string];
        dirtyPatch[key] = value as T[keyof T & string];
        changed[key] = value as T[keyof T & string];
      }
      emitPatch(changed);
      if (hostOptions?.commit ?? false) {
        commitDirtyPatch();
      }
    },
    commit(): void {
      assertActive();
      commitDirtyPatch();
    },
    dispatchCustomMessage(msg: unknown, buffers: Uint8Array[] = []): void {
      assertActive();
      const dataViews = toDataViews(buffers);
      for (const listener of customListeners) {
        listener(msg, dataViews);
      }
    },
    onSave(listener: SaveListener<T>): () => void {
      return registerListener(saveListeners, listener);
    },
    onSend(listener: SendListener): () => void {
      return registerListener(sendListeners, listener);
    },
    onStateChange(listener: SaveListener<T>): () => void {
      return registerListener(stateChangeListeners, listener);
    },
    registerCommand(name: string, handler: ClientCommand): () => void {
      commands.set(name, handler);
      return () => {
        commands.delete(name);
      };
    },
  };

  return {
    host,
    async mount(el: HTMLElement): Promise<{ unmount(): Promise<void> }> {
      assertActive();
      await mounts.get(el)?.unmount();
      el.replaceChildren();
      const renderCleanup = await widget.render?.({ model, el, experimental });
      const handle = {
        async unmount() {
          if (!mounts.has(el)) {
            return;
          }
          mounts.delete(el);
          await runCleanup(renderCleanup);
          el.replaceChildren();
        },
      };
      mounts.set(el, handle);
      return handle;
    },
    async destroy() {
      if (destroyed) {
        return;
      }
      destroyed = true;
      for (const mount of mounts.values()) {
        await mount.unmount();
      }
      mounts.clear();
      await runCleanup(initializeCleanup);
    },
  };
}
