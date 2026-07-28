import type { AnyModel, AnyWidget, ResolvedWidget } from "@anywidget/types";

import { cloneModelState, parseDataUrl, type AnyWidgetSnapshot, type EsmSpec } from "../payload.js";
import { abortError, combineAbortSignals, raceAbort } from "./abort.js";
import { WidgetBinding, resolveAnyWidgetModule } from "./binding.js";
import { createHost, type WidgetResolver } from "./host.js";
import { type ModelResolver, type ModelState, StaticModel } from "./model.js";

interface BindingEntry {
  readonly controller: AbortController;
  readonly promise: Promise<WidgetBinding>;
  readonly state: { binding?: WidgetBinding };
}

interface StyleMount {
  references: number;
  update(css: string): void;
  dispose(): void;
}

interface ViewRuntime {
  readonly model: StaticModel;
  readonly esmSpec: EsmSpec | undefined;
  readonly styleMounts: Map<Document | ShadowRoot, StyleMount>;
  css: string;
}

const ACTIVE_MOUNTS = new WeakSet<HTMLElement>();

export interface MountedRuntime<State extends ModelState, Exports> {
  readonly model: AnyModel<State>;
  readonly exports: Exports;
  dispose(): Promise<void>;
}

export async function mountSnapshot<State extends ModelState, Exports>(
  snapshot: AnyWidgetSnapshot,
  element: HTMLElement,
  options: { readonly signal?: AbortSignal } = {},
): Promise<MountedRuntime<State, Exports>> {
  assertBrowserElement(element);
  options.signal?.throwIfAborted();
  if (ACTIVE_MOUNTS.has(element)) {
    throw new Error("Dispose the existing AnyWidget mount before reusing its element.");
  }
  const registry = new StaticRegistry(snapshot);
  ACTIVE_MOUNTS.add(element);
  let disposePromise: Promise<void> | undefined;
  const dispose = (): Promise<void> => {
    disposePromise ??= registry.dispose().finally(() => ACTIVE_MOUNTS.delete(element));
    return disposePromise;
  };
  const onAbort = () => {
    void dispose().catch(() => undefined);
  };
  options.signal?.addEventListener("abort", onAbort, { once: true });

  try {
    const binding = await registry.getBinding(snapshot.rootModelId);
    await registry.createView(snapshot.rootModelId, element, registry.signal);
    if (registry.signal.aborted) throw abortError("AnyWidget mount was disposed.");
    options.signal?.throwIfAborted();
    const model = await registry.getModel(snapshot.rootModelId);
    if (registry.signal.aborted) throw abortError("AnyWidget mount was disposed.");
    options.signal?.throwIfAborted();
    return Object.freeze({
      model: model as AnyModel<State>,
      exports: binding.exports as Exports,
      async dispose() {
        options.signal?.removeEventListener("abort", onAbort);
        await dispose();
      },
    });
  } catch (error) {
    options.signal?.removeEventListener("abort", onAbort);
    try {
      await dispose();
    } catch (cleanupError) {
      throw new AggregateError([error, cleanupError], "AnyWidget mount and cleanup failed.");
    }
    throw error;
  }
}

export class StaticRegistry implements ModelResolver, WidgetResolver {
  readonly #snapshot: AnyWidgetSnapshot;
  readonly #controller = new AbortController();
  readonly #runtimes = new Map<string, ViewRuntime>();
  readonly #bindings = new Map<string, BindingEntry>();
  readonly #bindingOrder: WidgetBinding[] = [];
  readonly #modulePromises = new Map<string, Map<string, Promise<AnyWidget>>>();
  readonly #objectUrls = new Set<string>();
  #disposePromise: Promise<void> | undefined;
  #disposing = false;

  constructor(snapshot: AnyWidgetSnapshot) {
    this.#snapshot = snapshot;
    for (const model of snapshot.models.values()) {
      const state = cloneModelState(model.state as ModelState);
      const runtime: ViewRuntime = {
        model: new StaticModel(state, this, this.#controller.signal),
        esmSpec: model.esmSpec,
        styleMounts: new Map(),
        css: typeof state._css === "string" ? state._css : "",
      };
      runtime.model.on(
        "change:_css",
        () => {
          const css = runtime.model.get("_css");
          runtime.css = typeof css === "string" ? css : "";
          for (const mount of runtime.styleMounts.values()) mount.update(runtime.css);
        },
        { signal: this.#controller.signal },
      );
      this.#runtimes.set(model.id, runtime);
    }
  }

  get signal(): AbortSignal {
    return this.#controller.signal;
  }

  async getModel(modelId: string): Promise<AnyModel<ModelState>> {
    return this.#runtime(modelId).model;
  }

  async getWidget<Exports = unknown>(modelId: string): Promise<ResolvedWidget<Exports>> {
    const binding = await this.getBinding(modelId);
    return {
      exports: binding.exports as Exports,
      render: async ({ el, signal }) => {
        await this.createView(modelId, el, signal ?? this.#controller.signal);
      },
    };
  }

  getBinding(modelId: string): Promise<WidgetBinding> {
    if (this.#disposing) {
      return Promise.reject(abortError("AnyWidget registry was disposed."));
    }
    const existing = this.#bindings.get(modelId);
    if (existing !== undefined) return existing.promise;
    const runtime = this.#runtime(modelId);
    if (runtime.esmSpec === undefined) {
      return Promise.reject(
        new Error(`AnyWidget model ${JSON.stringify(modelId)} has no ESM spec.`),
      );
    }
    const controller = new AbortController();
    const onDispose = () => controller.abort(this.#controller.signal.reason);
    if (this.#controller.signal.aborted) onDispose();
    else this.#controller.signal.addEventListener("abort", onDispose, { once: true });

    // A dynamic import cannot be cancelled once browser module evaluation starts.
    // Race only the binding's interest in that import so disposal can settle while
    // the browser finishes, rejects, or leaves the module pending in the background.
    const state: BindingEntry["state"] = {};
    const promise = raceAbort(
      this.#loadWidget(runtime.esmSpec),
      controller.signal,
      "AnyWidget binding was disposed.",
    )
      .then((widget) =>
        WidgetBinding.create({
          widget,
          model: runtime.model,
          createHost: (signal) => createHost(this, signal),
          controller,
        }),
      )
      .then((binding) => {
        state.binding = binding;
        this.#bindingOrder.push(binding);
        return binding;
      })
      .catch((error) => {
        controller.abort();
        this.#bindings.delete(modelId);
        throw error;
      })
      .finally(() => this.#controller.signal.removeEventListener("abort", onDispose));
    this.#bindings.set(modelId, { controller, promise, state });
    return promise;
  }

  async createView(modelId: string, element: HTMLElement, signal: AbortSignal): Promise<void> {
    if (signal.aborted || this.#controller.signal.aborted) {
      throw abortError("AnyWidget view was disposed.");
    }
    const runtime = this.#runtime(modelId);
    const controller = new AbortController();
    const viewSignal = combineAbortSignals([signal, this.#controller.signal, controller.signal]);
    try {
      this.#mountStyle(runtime, element, viewSignal);
      const binding = await this.getBinding(modelId);
      await binding.createView(element, viewSignal);
    } catch (error) {
      controller.abort(error);
      throw error;
    }
  }

  dispose(): Promise<void> {
    if (this.#disposePromise === undefined) {
      this.#disposing = true;
      const entries = [...this.#bindings.values()];
      for (const entry of entries) {
        if (entry.state.binding === undefined) entry.controller.abort();
      }
      this.#disposePromise = this.#dispose(entries);
    }
    return this.#disposePromise;
  }

  async #loadWidget(spec: EsmSpec): Promise<AnyWidget> {
    if (this.#disposing) throw abortError("AnyWidget registry was disposed.");
    let modulesByUrl = this.#modulePromises.get(spec.hash);
    const existing = modulesByUrl?.get(spec.url);
    if (existing !== undefined) return existing;
    modulesByUrl ??= new Map();
    this.#modulePromises.set(spec.hash, modulesByUrl);
    const promise = this.#importModule(spec)
      .then((module) => resolveAnyWidgetModule(module, spec.url))
      .catch((error) => {
        const current = this.#modulePromises.get(spec.hash);
        if (current?.get(spec.url) === promise) {
          current.delete(spec.url);
          if (current.size === 0) this.#modulePromises.delete(spec.hash);
        }
        throw error;
      });
    modulesByUrl.set(spec.url, promise);
    return promise;
  }

  async #importModule(spec: EsmSpec): Promise<unknown> {
    if (this.#disposing) throw abortError("AnyWidget registry was disposed.");
    const embedded = this.#snapshot.files[spec.url];
    let moduleUrl = spec.url;
    if (embedded !== undefined) {
      moduleUrl = URL.createObjectURL(dataUrlToBlob(embedded));
      this.#objectUrls.add(moduleUrl);
    }
    // Keep the runtime URL opaque to each supported bundler so the browser
    // performs the import when this mount requests it.
    return import(
      /* @vite-ignore */ /* webpackIgnore: true */ /* turbopackIgnore: true */ moduleUrl
    );
  }

  #runtime(modelId: string): ViewRuntime {
    const runtime = this.#runtimes.get(modelId);
    if (runtime === undefined) {
      throw new Error(`AnyWidget model ${JSON.stringify(modelId)} is missing from this mount.`);
    }
    return runtime;
  }

  #mountStyle(runtime: ViewRuntime, element: HTMLElement, signal: AbortSignal): void {
    const root = element.getRootNode();
    if (!isDocument(root) && !isShadowRoot(root)) return;
    let mount = runtime.styleMounts.get(root);
    if (mount === undefined) {
      mount = createStyleMount(root, runtime.css);
      runtime.styleMounts.set(root, mount);
    }
    mount.references += 1;
    let released = false;
    signal.addEventListener(
      "abort",
      () => {
        if (released) return;
        released = true;
        const current = runtime.styleMounts.get(root);
        if (current === undefined) return;
        current.references -= 1;
        if (current.references === 0) {
          current.dispose();
          runtime.styleMounts.delete(root);
        }
      },
      { once: true },
    );
  }

  async #dispose(entries: readonly BindingEntry[]): Promise<void> {
    const errors: unknown[] = [];
    await Promise.allSettled(entries.map((entry) => entry.promise));
    for (const binding of [...this.#bindingOrder].reverse()) {
      try {
        // Child bindings initialize after their parents and settle first during teardown.
        // oxlint-disable-next-line eslint/no-await-in-loop
        await binding.destroy();
      } catch (error) {
        errors.push(error);
      }
    }
    this.#controller.abort();
    for (const runtime of this.#runtimes.values()) {
      for (const mount of runtime.styleMounts.values()) mount.dispose();
      runtime.styleMounts.clear();
    }
    // Revoke object URLs immediately. Waiting for their imports would make disposal
    // depend on uncancellable top-level await in notebook-authored JavaScript.
    for (const url of this.#objectUrls) URL.revokeObjectURL(url);
    this.#objectUrls.clear();
    this.#modulePromises.clear();
    if (errors.length > 0) throw new AggregateError(errors, "AnyWidget cleanup failed.");
  }
}

function dataUrlToBlob(dataUrl: string): Blob {
  const { body, isBase64, mediaType } = parseDataUrl(dataUrl, "AnyWidget ESM data URL");
  // Snapshot validation completes before registry construction, so decoding
  // cannot begin until the embedded data URL has passed payload validation.
  const bytes = isBase64 ? base64Bytes(body) : new TextEncoder().encode(decodeURIComponent(body));
  return new Blob([bytes], { type: mediaType });
}

function base64Bytes(value: string): Uint8Array<ArrayBuffer> {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function createStyleMount(root: Document | ShadowRoot, css: string): StyleMount {
  const document = isDocument(root) ? root : root.ownerDocument;
  const style = document.createElement("style");
  style.textContent = css;
  if (isDocument(root)) root.head.append(style);
  else root.append(style);
  return {
    references: 0,
    update(nextCss) {
      style.textContent = nextCss;
    },
    dispose() {
      style.remove();
    },
  };
}

function isDocument(node: Node): node is Document {
  return node.nodeType === 9 && "head" in node;
}

function isShadowRoot(node: Node): node is ShadowRoot {
  return node.nodeType === 11 && "host" in node;
}

function assertBrowserElement(element: HTMLElement): void {
  if (
    typeof document === "undefined" ||
    typeof window === "undefined" ||
    element === null ||
    typeof element !== "object" ||
    element.nodeType !== 1 ||
    typeof element.replaceChildren !== "function"
  ) {
    throw new TypeError("AnyWidget mount requires a browser element.");
  }
}
