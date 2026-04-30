export type CleanupFn = () => void | Promise<void>;
export type Cleanup = void | Promise<void | CleanupFn> | CleanupFn;

export type ClientCommand = (
  msg: unknown,
  options: { buffers: Uint8Array[]; signal?: AbortSignal },
) => Promise<[unknown, Uint8Array[]]> | [unknown, Uint8Array[]];

export type ClientHost<T extends object> = {
  getState(): T;
  setState(patch: Partial<T>, options?: { commit?: boolean }): void;
  replaceState(next: T, options?: { commit?: boolean }): void;
  commit(): void;
  dispatchCustomMessage(msg: unknown, buffers?: Uint8Array[]): void;
  onSave(listener: (patch: Partial<T>, fullState: T) => void): () => void;
  onSend(listener: (msg: unknown, buffers: Uint8Array[]) => void): () => void;
  onStateChange(listener: (patch: Partial<T>, fullState: T) => void): () => void;
  registerCommand(name: string, handler: ClientCommand): () => void;
};

export type StandaloneWidget<T extends object> = {
  host: ClientHost<T>;
  mount(el: HTMLElement): Promise<{ unmount(): Promise<void> }>;
  destroy(): Promise<void>;
};

export type AnyModel<T extends object> = {
  get<K extends keyof T>(key: K): T[K];
  set<K extends keyof T>(key: K, value: T[K]): void;
  on(eventName: "msg:custom", callback: (msg: unknown, buffers: DataView[]) => void): void;
  on(eventName: `change:${string}`, callback: () => void): void;
  off(eventName?: string | null, callback?: ((...args: unknown[]) => void) | null): void;
  save_changes(): void;
  send(content: unknown, callbacks?: unknown, buffers?: ArrayBuffer[] | ArrayBufferView[]): void;
  widget_manager: {
    get_model(model_id: string): Promise<never>;
  };
};

export type RenderContext<T extends object> = {
  model: AnyModel<T>;
  el: HTMLElement;
  experimental: {
    invoke: <R>(
      name: string,
      msg?: unknown,
      options?: { buffers?: DataView[]; signal?: AbortSignal },
    ) => Promise<[R, DataView[]]>;
  };
};

export type InitializeContext<T extends object> = {
  model: AnyModel<T>;
  experimental: RenderContext<T>["experimental"];
};

export type WidgetDefinition<T extends object> = {
  initialize?: (context: InitializeContext<T>) => Cleanup;
  render?: (context: RenderContext<T>) => Cleanup;
};

export type WidgetModuleNamespace<T extends object> = {
  default?: WidgetDefinition<T> | (() => Promise<WidgetDefinition<T>> | WidgetDefinition<T>);
  render?: WidgetDefinition<T>["render"];
};

export type CreateStandaloneWidgetOptions<T extends object> = {
  widgetModule: WidgetModuleNamespace<T>;
  anywidgetId: string;
  initialState: T;
  input?: {
    state?: Partial<T>;
    commands?: Record<string, ClientCommand>;
  };
  inlineCssText?: string | null;
};
