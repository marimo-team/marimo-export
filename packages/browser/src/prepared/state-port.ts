import type { JsonObject } from "@marimo-team/portable-json";

import type { PreparedPublication } from "./manifest.js";
import { isPreparedAbort, linkPreparedAbort, throwIfPreparedAborted } from "./cancellation.js";

export type PreparedStateChangeReason = "start" | "state" | "publication";

export interface PreparedStateChange {
  readonly previous: PreparedPublication | undefined;
  readonly next: PreparedPublication;
  readonly reason: PreparedStateChangeReason;
}

export interface PreparedStatePort {
  /** Load every required output, then publish the complete state atomically. */
  apply(change: PreparedStateChange, signal: AbortSignal): Promise<void>;
  /** Restore the last committed state after a rejected application request. */
  restore?(publication: PreparedPublication): void | Promise<void>;
  dispose?(): void | Promise<void>;
}

export interface PreparedStateSnapshot {
  readonly current: PreparedPublication | undefined;
  readonly pendingInputs: JsonObject | undefined;
  readonly transition: {
    readonly generation: number;
    readonly target: PreparedPublication | undefined;
    readonly active: boolean;
  };
  readonly disposed: boolean;
}

export class PreparedStateTransitions {
  #current: PreparedPublication | undefined;
  #target: PreparedPublication | undefined;
  #controller: AbortController | undefined;
  #generation = 0;
  #operation: Promise<void> = Promise.resolve();
  #closed = false;

  constructor(
    private readonly port: PreparedStatePort,
    private readonly lifecycle: AbortSignal,
  ) {}

  get current(): PreparedPublication | undefined {
    return this.#current;
  }

  get target(): PreparedPublication | undefined {
    return this.#target;
  }

  snapshot(pendingInputs: JsonObject | undefined, disposed: boolean): PreparedStateSnapshot {
    return Object.freeze({
      current: this.#current,
      pendingInputs,
      transition: Object.freeze({
        generation: this.#generation,
        target: this.#target,
        active: this.#controller !== undefined,
      }),
      disposed,
    });
  }

  setMetadata(publication: PreparedPublication): void {
    if (this.#target !== undefined) {
      throw new Error("Prepared state transition is active.");
    }
    this.#current = publication;
  }

  apply(
    next: PreparedPublication,
    reason: PreparedStateChangeReason,
    parentSignal: AbortSignal,
  ): Promise<void> {
    if (this.#closed) {
      throw new Error("Prepared state transitions are closed.");
    }
    throwIfPreparedAborted(parentSignal);
    this.cancel(new DOMException("Prepared state transition superseded", "AbortError"));
    const generation = this.#generation;
    const controller = new AbortController();
    const unlinkLifecycle = linkPreparedAbort(controller, this.lifecycle);
    const unlinkParent = linkPreparedAbort(controller, parentSignal);
    this.#controller = controller;
    this.#target = next;
    const previousOperation = this.#operation;
    const operation = previousOperation
      .catch(() => {})
      .then(async () => {
        throwIfPreparedAborted(controller.signal);
        const previous = this.#current;
        try {
          await this.port.apply({ previous, next, reason }, controller.signal);
          throwIfPreparedAborted(controller.signal);
        } catch (error) {
          if (generation === this.#generation && !this.#closed && !isPreparedAbort(error)) {
            await this.restoreAfter(error);
          }
          throw error;
        }
        if (generation === this.#generation && !this.#closed) {
          this.#current = next;
        }
      });
    this.#operation = operation.finally(() => {
      unlinkLifecycle();
      unlinkParent();
      if (this.#controller === controller) {
        this.#controller = undefined;
      }
      if (generation === this.#generation) {
        this.#target = undefined;
      }
    });
    return this.#operation;
  }

  async restoreAfter(primary: unknown): Promise<void> {
    try {
      await this.restore();
    } catch (cleanup) {
      throw new AggregateError(
        [primary, cleanup],
        "Prepared state transition and restoration failed.",
      );
    }
  }

  async restore(): Promise<void> {
    if (this.#current !== undefined) {
      await this.port.restore?.(this.#current);
    }
  }

  cancel(reason: Error | DOMException): void {
    this.#generation += 1;
    this.#target = undefined;
    this.#controller?.abort(reason);
    this.#controller = undefined;
  }

  settle(): Promise<readonly PromiseSettledResult<void>[]> {
    return Promise.allSettled([this.#operation]);
  }

  wait(): Promise<void> {
    return this.#operation;
  }

  async close(reason: Error | DOMException): Promise<void> {
    this.#closed = true;
    this.cancel(reason);
    await this.#operation.catch(() => {});
    this.#current = undefined;
    this.#target = undefined;
    this.#controller = undefined;
    this.#operation = Promise.resolve();
  }
}
