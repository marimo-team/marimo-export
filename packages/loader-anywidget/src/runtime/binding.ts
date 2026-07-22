import type { AnyWidget, Experimental } from "@anywidget/types";

import type { Host } from "@anywidget/types";
import { modelProxy } from "./model-proxy.js";
import type { ModelState, StaticModel } from "./model.js";

type Cleanup = () => void | PromiseLike<void>;
type ResolvedDefinition<T extends ModelState> = Exclude<
  AnyWidget<T>,
  (...args: never[]) => unknown
>;

const experimental: Experimental = {
  async invoke() {
    throw new Error("Static AnyWidget projections cannot invoke Python.");
  },
};

export function resolveAnyWidgetModule<T extends ModelState>(
  module: unknown,
  moduleUrl: string,
): AnyWidget<T> {
  if (!isRecord(module)) throw invalidModule(moduleUrl);
  if (isAnyWidget(module.default)) return module.default as AnyWidget<T>;
  if (module.default !== undefined) throw invalidModule(moduleUrl);

  const render = typeof module.render === "function" ? module.render : undefined;
  const initialize = typeof module.initialize === "function" ? module.initialize : undefined;
  if (render === undefined && initialize === undefined) throw invalidModule(moduleUrl);
  return { render, initialize } as AnyWidget<T>;
}

export class WidgetBinding<T extends ModelState = ModelState> {
  readonly #controller: AbortController;
  readonly #widget: ResolvedDefinition<T>;
  readonly #model: StaticModel<T>;
  readonly #createHost: (signal: AbortSignal) => Host;
  readonly #exports: unknown;
  readonly #cleanupTasks = new Set<Promise<void>>();
  readonly #cleanupErrors: unknown[] = [];
  readonly #viewTasks = new Set<Promise<void>>();
  #destroyPromise: Promise<void> | undefined;

  private constructor(options: {
    controller: AbortController;
    widget: ResolvedDefinition<T>;
    model: StaticModel<T>;
    createHost: (signal: AbortSignal) => Host;
    exports: unknown;
  }) {
    this.#controller = options.controller;
    this.#widget = options.widget;
    this.#model = options.model;
    this.#createHost = options.createHost;
    this.#exports = options.exports;
  }

  static async create<T extends ModelState>(options: {
    widget: AnyWidget<T>;
    model: StaticModel<T>;
    createHost: (signal: AbortSignal) => Host;
    controller: AbortController;
  }): Promise<WidgetBinding<T>> {
    const { controller, model, createHost } = options;
    const signal = controller.signal;
    const definitionTask = Promise.resolve(
      typeof options.widget === "function" ? options.widget() : options.widget,
    );
    const widget = await raceAbort(definitionTask, signal, "AnyWidget binding was disposed.");

    let lateInitializeCleanup: Promise<void> | undefined;
    const settleLateInitializeCleanup = (cleanup: Cleanup): Promise<void> => {
      lateInitializeCleanup ??= settleCleanup(cleanup);
      return lateInitializeCleanup;
    };
    const initializeTask = Promise.resolve(
      widget.initialize?.({
        model: modelProxy(model, signal),
        experimental,
        signal,
      }),
    );
    initializeTask
      .then(async (result) => {
        if (signal.aborted && isCleanup(result)) await settleLateInitializeCleanup(result);
      })
      .catch(() => undefined);
    const initializeResult = await raceAbort(
      initializeTask,
      signal,
      "AnyWidget binding was disposed.",
    );
    if (signal.aborted) {
      if (isCleanup(initializeResult)) await settleLateInitializeCleanup(initializeResult);
      throw abortReason(signal, "AnyWidget binding was disposed.");
    }

    let exports: unknown;
    let initializeCleanup: Cleanup | undefined;
    if (isCleanup(initializeResult)) {
      initializeCleanup = initializeResult;
    } else if (typeof initializeResult === "object" && initializeResult !== null) {
      exports = initializeResult;
    }

    const binding = new WidgetBinding<T>({
      controller,
      widget,
      model,
      createHost,
      exports,
    });
    if (initializeCleanup !== undefined) {
      signal.addEventListener(
        "abort",
        () => binding.#trackCleanup(initializeCleanup, "initialize"),
        { once: true },
      );
    }
    return binding;
  }

  get exports(): unknown {
    return this.#exports;
  }

  async createView(element: HTMLElement, signal: AbortSignal): Promise<void> {
    const task = this.#createView(element, signal);
    this.#viewTasks.add(task);
    try {
      await task;
    } finally {
      this.#viewTasks.delete(task);
    }
  }

  destroy(): Promise<void> {
    this.#destroyPromise ??= this.#destroy();
    return this.#destroyPromise;
  }

  async #createView(element: HTMLElement, signal: AbortSignal): Promise<void> {
    const renderSignal = abortSignalAny([signal, this.#controller.signal]);
    if (renderSignal.aborted) throw abortReason(renderSignal, "AnyWidget view was disposed.");
    element.replaceChildren();

    let renderCleanupTask: Promise<void> | undefined;
    const settleRenderCleanup = (cleanup: Cleanup): Promise<void> => {
      renderCleanupTask ??= this.#trackCleanup(cleanup, "render");
      return renderCleanupTask;
    };
    const renderTask = Promise.resolve(
      this.#widget.render?.({
        model: modelProxy(this.#model, renderSignal),
        el: element,
        experimental,
        signal: renderSignal,
        host: this.#createHost(renderSignal),
      }),
    );
    renderTask
      .then((cleanup) => {
        if (renderSignal.aborted && isCleanup(cleanup)) {
          void settleRenderCleanup(cleanup);
        }
      })
      .catch(() => undefined);

    let cleanup: unknown;
    try {
      cleanup = await raceAbort(renderTask, renderSignal, "AnyWidget view was disposed.");
    } catch (error) {
      element.replaceChildren();
      throw error;
    }

    const clear = () => element.replaceChildren();
    if (!isCleanup(cleanup)) {
      renderSignal.addEventListener("abort", clear, { once: true });
      return;
    }
    const release = () => {
      const task = settleRenderCleanup(cleanup);
      void task.finally(clear);
    };
    if (renderSignal.aborted) {
      release();
      return;
    }
    renderSignal.addEventListener("abort", release, { once: true });
  }

  #trackCleanup(cleanup: Cleanup, phase: string): Promise<void> {
    const task = settleCleanup(cleanup).catch((error) => {
      this.#cleanupErrors.push(new Error(`AnyWidget ${phase} cleanup failed.`, { cause: error }));
    });
    this.#cleanupTasks.add(task);
    void task.finally(() => this.#cleanupTasks.delete(task));
    return task;
  }

  async #destroy(): Promise<void> {
    this.#controller.abort();
    await Promise.allSettled(this.#viewTasks);
    // Cleanup callbacks can enqueue follow-up cleanup work.
    // oxlint-disable-next-line eslint/no-await-in-loop
    while (this.#cleanupTasks.size > 0) await Promise.all(this.#cleanupTasks);
    if (this.#cleanupErrors.length > 0) {
      throw new AggregateError(this.#cleanupErrors, "AnyWidget cleanup failed.");
    }
  }
}

function isAnyWidget(value: unknown): boolean {
  return (
    typeof value === "function" ||
    (isRecord(value) &&
      (typeof value.render === "function" || typeof value.initialize === "function"))
  );
}

function invalidModule(url: string): Error {
  return new Error(
    `AnyWidget module ${JSON.stringify(url)} must default-export a factory or an object with render or initialize.`,
  );
}

function isCleanup(value: unknown): value is Cleanup {
  return typeof value === "function";
}

async function settleCleanup(cleanup: Cleanup): Promise<void> {
  await cleanup();
}

async function raceAbort<T>(task: Promise<T>, signal: AbortSignal, message: string): Promise<T> {
  if (signal.aborted) throw abortReason(signal, message);
  let onAbort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    onAbort = () => reject(abortReason(signal, message));
    signal.addEventListener("abort", onAbort, { once: true });
  });
  try {
    return await Promise.race([task, aborted]);
  } finally {
    if (onAbort !== undefined) signal.removeEventListener("abort", onAbort);
  }
}

function abortSignalAny(signals: readonly AbortSignal[]): AbortSignal {
  if (typeof AbortSignal.any === "function") return AbortSignal.any([...signals]);
  const controller = new AbortController();
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort(signal.reason);
      return controller.signal;
    }
    signal.addEventListener("abort", () => controller.abort(signal.reason), { once: true });
  }
  return controller.signal;
}

function abortReason(signal: AbortSignal, message: string): Error {
  return signal.reason instanceof Error
    ? signal.reason
    : Object.assign(new Error(message), { name: "AbortError" });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
