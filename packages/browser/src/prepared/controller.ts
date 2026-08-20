import type { JsonObject, JsonValue } from "@marimo-team/portable-json";

import {
  disposePreparedOnAbort,
  preparedAbortReason,
  throwIfPreparedAborted,
} from "./cancellation.js";
import { samePreparedInputs } from "./control.js";
import type { PreparedPublication } from "./manifest.js";
import {
  isPreparedStateUnavailable,
  mergePreparedInputs,
  pendingInputsAfterFailure,
  pendingInputsForPublication,
  selectPendingPublication,
  withPreparedState,
} from "./state-selection.js";
import type { PreparedStatePort, PreparedStateSnapshot } from "./state-port.js";
import { PreparedStateTransitions } from "./state-port.js";
import { preparedControlPatch, preparedQueryPatch } from "./state-update.js";

export class PreparedStateController {
  readonly #lifecycle = new AbortController();
  readonly #port: PreparedStatePort;
  readonly #transitions: PreparedStateTransitions;
  #pendingInputs: JsonObject | undefined;
  #disposed = false;
  #disposal: Promise<void> | undefined;
  #unlinkLifecycle = () => {};

  constructor(port: PreparedStatePort, signal?: AbortSignal) {
    this.#port = port;
    this.#transitions = new PreparedStateTransitions(port, this.#lifecycle.signal);
    if (signal !== undefined) {
      this.#unlinkLifecycle = disposePreparedOnAbort(signal, this.#lifecycle, () => this.dispose());
    }
  }

  snapshot(): PreparedStateSnapshot {
    return this.#transitions.snapshot(this.#pendingInputs, this.#disposed);
  }

  start(
    publication: PreparedPublication,
    signal: AbortSignal = this.#lifecycle.signal,
  ): Promise<void> {
    this.#requireActive();
    throwIfPreparedAborted(signal);
    if (this.#transitions.current !== undefined || this.#transitions.target !== undefined) {
      throw new Error("Prepared state controller has already started.");
    }
    return this.#apply(publication, "start", signal);
  }

  async updateInputs(
    patch: JsonObject,
    signal: AbortSignal = this.#lifecycle.signal,
  ): Promise<void> {
    this.#requireActive();
    throwIfPreparedAborted(signal);
    const source = this.#transitions.target ?? this.#requireCurrent();
    const base = this.#pendingInputs ?? source.state.inputs;
    const requested = mergePreparedInputs(base, patch);
    this.#pendingInputs = requested;
    if (samePreparedInputs(requested, source.state.inputs)) {
      if (this.#transitions.target !== undefined) {
        await this.#transitions.wait();
      } else {
        this.#pendingInputs = undefined;
        await this.#transitions.restore();
      }
      return;
    }
    let next: PreparedPublication;
    try {
      next = withPreparedState(source, source.notebookExport.resolve(requested));
    } catch (error) {
      if (!isPreparedStateUnavailable(error)) {
        this.#pendingInputs = undefined;
      }
      await this.#transitions.restoreAfter(error);
      throw error;
    }
    try {
      await this.#apply(next, "state", signal);
    } catch (error) {
      this.#pendingInputs = pendingInputsAfterFailure(
        this.#pendingInputs,
        requested,
        error,
        signal.aborted,
      );
      throw error;
    }
  }

  async updateControl(
    objectId: string,
    value: JsonValue,
    signal: AbortSignal = this.#lifecycle.signal,
  ): Promise<boolean> {
    this.#requireActive();
    throwIfPreparedAborted(signal);
    const source = this.#transitions.target ?? this.#requireCurrent();
    const patch = preparedControlPatch(
      source,
      this.#pendingInputs ?? source.state.inputs,
      objectId,
      value,
    );
    if (patch === undefined) {
      return false;
    }
    await this.updateInputs(patch, signal);
    return true;
  }

  async updateQuery(query: string, signal: AbortSignal = this.#lifecycle.signal): Promise<boolean> {
    this.#requireActive();
    throwIfPreparedAborted(signal);
    const source = this.#transitions.target ?? this.#requireCurrent();
    const patch = preparedQueryPatch(source, query);
    if (patch === undefined) {
      return false;
    }
    await this.updateInputs(patch, signal);
    return true;
  }

  async replacePublication(
    publication: PreparedPublication,
    signal: AbortSignal = this.#lifecycle.signal,
  ): Promise<void> {
    this.#requireActive();
    throwIfPreparedAborted(signal);
    const current = this.#transitions.current;
    this.#pendingInputs = pendingInputsForPublication(current, publication, this.#pendingInputs);
    const selected = selectPendingPublication(publication, this.#pendingInputs);
    if (
      this.#transitions.target === undefined &&
      current !== undefined &&
      current.notebookExport.identity === selected.notebookExport.identity &&
      current.state.fingerprint === selected.state.fingerprint
    ) {
      this.#transitions.setMetadata(selected);
      this.#clearSatisfiedPending(selected);
      return;
    }
    await this.#apply(selected, "publication", signal);
  }

  cancel(reason?: unknown): void {
    this.#transitions.cancel(preparedAbortReason(reason, "Prepared state transition superseded"));
  }

  settle(): Promise<readonly PromiseSettledResult<void>[]> {
    return this.#transitions.settle();
  }

  dispose(): Promise<void> {
    this.#disposal ??= this.#dispose();
    return this.#disposal;
  }

  async #apply(
    next: PreparedPublication,
    reason: "start" | "state" | "publication",
    signal: AbortSignal,
  ): Promise<void> {
    await this.#transitions.apply(next, reason, signal);
    this.#clearSatisfiedPending(next);
  }

  #clearSatisfiedPending(publication: PreparedPublication): void {
    if (
      this.#pendingInputs !== undefined &&
      samePreparedInputs(this.#pendingInputs, publication.state.inputs)
    ) {
      this.#pendingInputs = undefined;
    }
  }

  #requireCurrent(): PreparedPublication {
    const current = this.#transitions.current;
    if (current === undefined) {
      throw new Error("Prepared state controller has not started.");
    }
    return current;
  }

  #requireActive(): void {
    if (this.#disposed) {
      throw new Error("Prepared state controller is disposed.");
    }
  }

  async #dispose(): Promise<void> {
    this.#disposed = true;
    this.#lifecycle.abort(new DOMException("Prepared state controller disposed", "AbortError"));
    this.#unlinkLifecycle();
    this.#unlinkLifecycle = () => {};
    try {
      await this.#transitions.close(this.#lifecycle.signal.reason);
      await this.#port.dispose?.();
    } finally {
      this.#pendingInputs = undefined;
    }
  }
}
