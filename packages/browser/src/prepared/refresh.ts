import {
  disposePreparedOnAbort,
  linkPreparedAbort,
  throwIfPreparedAborted,
} from "./cancellation.js";
import type { PreparedStateController } from "./controller.js";
import { fetchPreparedExportManifest } from "./manifest-fetch.js";
import type { PreparedManifestFetchOptions } from "./manifest-fetch.js";
import {
  openPreparedPublication,
  preparedExportBase,
  resolvePreparedPublication,
} from "./manifest.js";
import type {
  OpenPreparedPublicationOptions,
  PreparedExportManifest,
  PreparedPublication,
} from "./manifest.js";
import { preservePreparedSelection } from "./state-selection.js";

export interface PreparedPublicationRefreshDependencies {
  fetchManifest(url: URL, options?: PreparedManifestFetchOptions): Promise<PreparedExportManifest>;
  openPublication(
    manifest: PreparedExportManifest,
    manifestUrl: URL,
    options?: OpenPreparedPublicationOptions,
  ): Promise<PreparedPublication>;
}

export interface PreparedPublicationRefreshOptions {
  readonly dependencies?: Partial<PreparedPublicationRefreshDependencies>;
  readonly fetch?: typeof globalThis.fetch;
  readonly openExport?: OpenPreparedPublicationOptions["openExport"];
  readonly signal?: AbortSignal;
  readonly onError?: (cause: unknown) => void;
}

interface MutableOpenOptions {
  fetch?: typeof globalThis.fetch;
  openExport?: NonNullable<OpenPreparedPublicationOptions["openExport"]>;
}

interface ManifestRequestOptions {
  fetch?: typeof globalThis.fetch;
  signal: AbortSignal;
}

export class PreparedPublicationRefresh {
  readonly #lifecycle = new AbortController();
  readonly #manifestUrl: URL;
  readonly #state: PreparedStateController;
  readonly #dependencies: PreparedPublicationRefreshDependencies;
  readonly #openOptions: OpenPreparedPublicationOptions;
  readonly #onError: (cause: unknown) => void;
  #operation: Promise<void> | undefined;
  #controller: AbortController | undefined;
  #timer: ReturnType<typeof setInterval> | undefined;
  #disposed = false;
  #unlinkLifecycle = () => {};

  constructor(
    manifestUrl: URL,
    state: PreparedStateController,
    options: PreparedPublicationRefreshOptions = {},
  ) {
    this.#manifestUrl = new URL(manifestUrl.href);
    this.#state = state;
    this.#dependencies = {
      fetchManifest: options.dependencies?.fetchManifest ?? fetchPreparedExportManifest,
      openPublication: options.dependencies?.openPublication ?? openPreparedPublication,
    };
    const openOptions: MutableOpenOptions = {};
    if (options.fetch !== undefined) openOptions.fetch = options.fetch;
    if (options.openExport !== undefined) openOptions.openExport = options.openExport;
    this.#openOptions = openOptions;
    this.#onError = options.onError ?? (() => {});
    if (options.signal !== undefined) {
      this.#unlinkLifecycle = disposePreparedOnAbort(options.signal, this.#lifecycle, () =>
        this.dispose(),
      );
    }
  }

  start(signal: AbortSignal = this.#lifecycle.signal): Promise<void> {
    throwIfPreparedAborted(signal);
    const snapshot = this.#state.snapshot();
    if (snapshot.current !== undefined || snapshot.transition.target !== undefined) {
      throw new Error("Prepared publication refresh has already started.");
    }
    return this.#operation ?? this.#begin("start", signal);
  }

  refresh(signal: AbortSignal = this.#lifecycle.signal): Promise<void> {
    if (this.#disposed) {
      return Promise.resolve();
    }
    throwIfPreparedAborted(signal);
    return this.#operation ?? this.#begin("refresh", signal);
  }

  settle(): Promise<readonly PromiseSettledResult<void>[]> {
    return Promise.allSettled(this.#operation === undefined ? [] : [this.#operation]);
  }

  syncPolling(): void {
    this.#clearPolling();
    if (this.#disposed) {
      return;
    }
    const interval = this.#state.snapshot().current?.manifest.refreshIntervalMs ?? 0;
    if (interval === 0) {
      return;
    }
    this.#timer = setInterval(() => {
      if (this.#disposed) {
        return;
      }
      void this.refresh().catch((error) => {
        if (!this.#lifecycle.signal.aborted) {
          this.#onError(error);
        }
      });
    }, interval);
  }

  async dispose(): Promise<void> {
    if (this.#disposed) {
      await this.settle();
      return;
    }
    this.#disposed = true;
    this.#clearPolling();
    this.#lifecycle.abort(new DOMException("Prepared publication refresh disposed", "AbortError"));
    this.#controller?.abort(this.#lifecycle.signal.reason);
    this.#unlinkLifecycle();
    this.#unlinkLifecycle = () => {};
    await this.settle();
  }

  #begin(kind: "start" | "refresh", parentSignal: AbortSignal): Promise<void> {
    throwIfPreparedAborted(parentSignal);
    const controller = new AbortController();
    const unlinkLifecycle = linkPreparedAbort(controller, this.#lifecycle.signal);
    const unlinkParent = linkPreparedAbort(controller, parentSignal);
    this.#controller = controller;
    let tracked: Promise<void>;
    const operation = this.#perform(kind, controller.signal);
    tracked = operation.finally(() => {
      unlinkLifecycle();
      unlinkParent();
      if (this.#controller === controller) {
        this.#controller = undefined;
      }
      if (this.#operation === tracked) {
        this.#operation = undefined;
      }
    });
    this.#operation = tracked;
    return tracked;
  }

  async #perform(kind: "start" | "refresh", signal: AbortSignal): Promise<void> {
    throwIfPreparedAborted(signal);
    const manifestOptions: ManifestRequestOptions = { signal };
    if (this.#openOptions.fetch !== undefined) manifestOptions.fetch = this.#openOptions.fetch;
    const manifest = await this.#dependencies.fetchManifest(this.#manifestUrl, manifestOptions);
    throwIfPreparedAborted(signal);
    if (this.#state.snapshot().transition.active) {
      await this.#state.settle();
      throwIfPreparedAborted(signal);
    }
    const current = this.#state.snapshot().current;
    let publication: PreparedPublication;
    if (
      current !== undefined &&
      current.notebookExport.identity === manifest.instance &&
      current.notebookExport.base.href === preparedExportBase(manifest, this.#manifestUrl).href
    ) {
      const resolved = resolvePreparedPublication(
        manifest,
        this.#manifestUrl,
        current.notebookExport,
      );
      publication = resolved;
    } else {
      publication = await this.#dependencies.openPublication(manifest, this.#manifestUrl, {
        ...this.#openOptions,
        signal,
      });
    }
    publication = preservePreparedSelection(current, publication);
    throwIfPreparedAborted(signal);
    if (kind === "start" && current === undefined) {
      await this.#state.start(publication, signal);
    } else {
      await this.#state.replacePublication(publication, signal);
    }
    throwIfPreparedAborted(signal);
    this.syncPolling();
  }

  #clearPolling(): void {
    if (this.#timer !== undefined) {
      clearInterval(this.#timer);
      this.#timer = undefined;
    }
  }
}
