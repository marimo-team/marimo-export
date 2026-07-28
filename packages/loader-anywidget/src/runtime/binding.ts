import type { AnyWidget, Experimental } from "@anywidget/types";

import type { Host } from "@anywidget/types";
import { abortReason, combineAbortSignals, raceAbort } from "./abort.js";
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
  #destroyed = false;

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
    void initializeTask
      .then(async (result) => {
        if (signal.aborted && isCleanup(result)) await settleLateInitializeCleanup(result);
      })
      .catch((error: unknown) => {
        if (signal.aborted) reportLateFailure("initialize", error);
      });
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
    const renderSignal = combineAbortSignals([signal, this.#controller.signal]);
    if (renderSignal.aborted) throw abortReason(renderSignal, "AnyWidget view was disposed.");
    element.replaceChildren();

    let renderCleanupTask: Promise<void> | undefined;
    const settleRenderCleanup = (cleanup: Cleanup): Promise<void> => {
      renderCleanupTask ??= this.#trackCleanup(cleanup, "render");
      return renderCleanupTask;
    };
    const renderTask = Promise.resolve().then(() =>
      this.#widget.render?.({
        model: modelProxy(this.#model, renderSignal),
        el: element,
        experimental,
        signal: renderSignal,
        host: this.#createHost(renderSignal),
      }),
    );
    void renderTask
      .then((cleanup) => {
        if (renderSignal.aborted && isCleanup(cleanup)) {
          return settleRenderCleanup(cleanup);
        }
        return undefined;
      })
      .catch((error: unknown) => {
        if (renderSignal.aborted) reportLateFailure("render", error);
      });

    let cleanup: unknown;
    try {
      cleanup = await raceAbort(renderTask, renderSignal, "AnyWidget view was disposed.");
    } catch (error) {
      element.replaceChildren();
      throw error;
    }

    const clear = () => element.replaceChildren();
    if (!isCleanup(cleanup)) {
      if (renderSignal.aborted) clear();
      else renderSignal.addEventListener("abort", clear, { once: true });
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
      const failure = new Error(`AnyWidget ${phase} cleanup failed.`, { cause: error });
      if (this.#destroyed) reportLateFailure(phase, failure);
      else this.#cleanupErrors.push(failure);
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
    this.#destroyed = true;
    if (this.#cleanupErrors.length > 0) {
      throw new AggregateError(this.#cleanupErrors, "AnyWidget cleanup failed.");
    }
  }
}

function reportLateFailure(phase: string, error: unknown): void {
  console.error(`AnyWidget ${phase} settled after its mount was disposed.`, error);
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
    `AnyWidget module ${quoteDiagnostic(url)} must default-export a factory or an object with render or initialize.`,
  );
}

function quoteDiagnostic(value: string): string {
  const limit = 128;
  let body = "";
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    let token: string;
    if (codeUnit === 0x22 || codeUnit === 0x5c) {
      token = `\\${value[index]}`;
    } else if (
      codeUnit <= 0x1f ||
      (codeUnit >= 0x7f && codeUnit <= 0x9f) ||
      (codeUnit >= 0xd800 && codeUnit <= 0xdfff)
    ) {
      if (
        codeUnit >= 0xd800 &&
        codeUnit <= 0xdbff &&
        index + 1 < value.length &&
        value.charCodeAt(index + 1) >= 0xdc00 &&
        value.charCodeAt(index + 1) <= 0xdfff
      ) {
        token = value.slice(index, index + 2);
        index += 1;
      } else {
        token = `\\u${codeUnit.toString(16).padStart(4, "0")}`;
      }
    } else {
      token = value[index]!;
    }
    if (body.length + token.length > limit - 5) return `"${body}..."`;
    body += token;
  }
  return `"${body}"`;
}

function isCleanup(value: unknown): value is Cleanup {
  return typeof value === "function";
}

async function settleCleanup(cleanup: Cleanup): Promise<void> {
  await cleanup();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
