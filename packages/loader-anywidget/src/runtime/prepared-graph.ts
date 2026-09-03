export interface PreparedWidgetGraphSnapshot<Record> {
  readonly files: Readonly<{ [path: string]: string }>;
  readonly records: ReadonlyMap<string, Record>;
}

declare const preparedWidgetGraphCheckpoint: unique symbol;

export interface PreparedWidgetGraphCheckpoint<Record> {
  readonly [preparedWidgetGraphCheckpoint]: Record;
}

export interface PreparedWidgetGraphPort<Record, LiveState> {
  id(record: Record): string;
  active(record: Record): boolean;
  same(left: Record, right: Record): boolean;
  changesModule(previous: Record, next: Record): boolean;
  capture(id: string): LiveState;
  merge(record: Record, state: LiveState): Record;
  replay(record: Record, signal?: AbortSignal): Promise<void>;
  restore(id: string, state: LiveState): void | Promise<void>;
  close(id: string): Promise<void>;
  setFiles(files: Readonly<{ [path: string]: string }>): void;
  validate?(record: Record, signal?: AbortSignal): Promise<void>;
  preflight?(record: Record, signal?: AbortSignal): Promise<void>;
}

import { abortReason } from "./abort.js";

export interface PreparedWidgetGraphReplacement<Record> {
  readonly mutated: boolean;
  readonly remount: boolean;
  commit(): Promise<PreparedWidgetGraphSnapshot<Record> | undefined>;
  rollback(): Promise<void>;
}

export class PreparedWidgetGraphReplacementError extends Error {
  readonly remount = true;

  constructor(cause: Error) {
    super("Prepared AnyWidget graph replacement requires a full remount", { cause });
    this.name = "PreparedWidgetGraphReplacementError";
  }
}

interface GraphChanges<Record> {
  readonly additions: readonly Record[];
  readonly stableUpdates: readonly Record[];
  readonly replacements: readonly Record[];
  readonly removals: readonly Record[];
}

interface LiveGraphState<LiveState> {
  readonly stable: ReadonlyMap<string, LiveState>;
  readonly replacements: ReadonlyMap<string, LiveState>;
  readonly removals: ReadonlyMap<string, LiveState>;
}

const emptyGraph = <Record>(): PreparedWidgetGraphSnapshot<Record> => ({
  files: Object.freeze({}),
  records: new Map(),
});

const graphFailure = (cause: unknown): Error =>
  cause instanceof Error ? cause : new Error(String(cause));

const sameFiles = (
  left: Readonly<Record<string, string>>,
  right: Readonly<Record<string, string>>,
): boolean => {
  const leftNames = Object.keys(left);
  const rightNames = Object.keys(right);
  return (
    leftNames.length === rightNames.length &&
    leftNames.every((name) => Object.hasOwn(right, name) && left[name] === right[name])
  );
};

const snapshotCopy = <Record>(
  snapshot: PreparedWidgetGraphSnapshot<Record>,
  port: PreparedWidgetGraphPort<Record, unknown>,
): PreparedWidgetGraphSnapshot<Record> => {
  const records = new Map<string, Record>();
  snapshot.records.forEach((record, key) => {
    const id = port.id(record);
    if (id !== key) {
      throw new Error(
        `Prepared AnyWidget graph record ${JSON.stringify(key)} has identity ${JSON.stringify(id)}`,
      );
    }
    records.set(id, record);
  });
  return Object.freeze({
    files: Object.freeze({ ...snapshot.files }),
    records,
  });
};

const changesFor = <Record, LiveState>(
  previous: PreparedWidgetGraphSnapshot<Record>,
  next: PreparedWidgetGraphSnapshot<Record>,
  port: PreparedWidgetGraphPort<Record, LiveState>,
): GraphChanges<Record> => {
  const additions: Record[] = [];
  const stableUpdates: Record[] = [];
  const replacements: Record[] = [];
  next.records.forEach((record, id) => {
    if (!port.active(record)) return;
    const current = previous.records.get(id);
    if (current === undefined || !port.active(current)) {
      additions.push(record);
      return;
    }
    if (port.same(current, record)) return;
    if (port.changesModule(current, record)) replacements.push(record);
    else stableUpdates.push(record);
  });
  return { additions, stableUpdates, replacements, removals: [] };
};

const currentRecordActive = <Record, LiveState>(
  next: PreparedWidgetGraphSnapshot<Record>,
  id: string,
  port: PreparedWidgetGraphPort<Record, LiveState>,
): boolean => {
  const record = next.records.get(id);
  return record !== undefined && port.active(record);
};

const plannedChanges = <Record, LiveState>(
  previous: PreparedWidgetGraphSnapshot<Record>,
  next: PreparedWidgetGraphSnapshot<Record>,
  port: PreparedWidgetGraphPort<Record, LiveState>,
): GraphChanges<Record> => {
  const changes = changesFor(previous, next, port);
  return {
    ...changes,
    removals: [...previous.records.entries()]
      .filter(([id, record]) => port.active(record) && !currentRecordActive(next, id, port))
      .map(([, record]) => record),
  };
};

const captureStates = <Record, LiveState>(
  changes: GraphChanges<Record>,
  port: PreparedWidgetGraphPort<Record, LiveState>,
): LiveGraphState<LiveState> => {
  const capture = (records: readonly Record[]): Map<string, LiveState> =>
    new Map(records.map((record) => [port.id(record), port.capture(port.id(record))]));
  return {
    stable: capture(changes.stableUpdates),
    replacements: capture(changes.replacements),
    removals: capture(changes.removals),
  };
};

const attempt = async (errors: Error[], action: () => void | Promise<void>): Promise<void> => {
  try {
    await action();
  } catch (error) {
    errors.push(graphFailure(error));
  }
};

const eachSequential = async <Value>(
  values: Iterable<Value>,
  action: (value: Value) => void | Promise<void>,
): Promise<void> => {
  for (const value of values) {
    // Model teardown and replay share registry identities, so order is part of the lifecycle.
    // oxlint-disable-next-line eslint/no-await-in-loop
    await action(value);
  }
};

const throwCleanup = (errors: readonly Error[], message: string): void => {
  if (errors.length === 1) throw errors[0];
  if (errors.length > 1) throw new AggregateError(errors, message);
};

const settledReplacement = <Record>(
  mutated: boolean,
  remount: boolean,
  settle: (
    commit: boolean,
  ) =>
    | PreparedWidgetGraphSnapshot<Record>
    | undefined
    | Promise<PreparedWidgetGraphSnapshot<Record> | undefined>,
): PreparedWidgetGraphReplacement<Record> => {
  let committed = false;
  let commitFailure: Error | undefined;
  let commitTask: Promise<PreparedWidgetGraphSnapshot<Record> | undefined> | undefined;
  let rollbackTask: Promise<void> | undefined;
  return Object.freeze({
    mutated,
    remount,
    commit() {
      if (rollbackTask !== undefined) return rollbackTask.then(() => undefined);
      commitTask ??= Promise.resolve()
        .then(() => settle(true))
        .then((result) => {
          committed = true;
          return result;
        })
        .catch((cause: unknown) => {
          commitFailure = graphFailure(cause);
          throw cause;
        });
      return commitTask;
    },
    rollback() {
      rollbackTask ??= (commitTask ?? Promise.resolve())
        .catch(() => {})
        .then(async () => {
          if (committed) return;
          try {
            await settle(false);
          } catch (error) {
            throw new PreparedWidgetGraphReplacementError(
              commitFailure === undefined
                ? graphFailure(error)
                : new AggregateError(
                    [commitFailure, graphFailure(error)],
                    "Prepared AnyWidget graph commit and rollback failed",
                  ),
            );
          }
        });
      return rollbackTask;
    },
  });
};

export class PreparedWidgetGraph<Record, LiveState> {
  readonly #checkpoints = new WeakMap<
    object,
    {
      readonly source: PreparedWidgetGraphSnapshot<Record>;
      readonly snapshot: PreparedWidgetGraphSnapshot<Record>;
    }
  >();
  readonly #port: PreparedWidgetGraphPort<Record, LiveState>;
  #current: PreparedWidgetGraphSnapshot<Record>;
  #active: AbortController | undefined;
  #operation: Promise<PreparedWidgetGraphReplacement<Record>> | undefined;
  #pending: PreparedWidgetGraphReplacement<Record> | undefined;
  #disposed = false;
  #disposal: Promise<void> | undefined;

  constructor(
    port: PreparedWidgetGraphPort<Record, LiveState>,
    initial: PreparedWidgetGraphSnapshot<Record> = emptyGraph<Record>(),
  ) {
    this.#port = port;
    this.#current = snapshotCopy(initial, port);
    this.#port.setFiles(this.#current.files);
  }

  checkpoint(): PreparedWidgetGraphCheckpoint<Record> {
    this.#requireIdle();
    const records = new Map(this.#current.records);
    this.#current.records.forEach((record, id) => {
      if (this.#port.active(record)) {
        records.set(id, this.#port.merge(record, this.#port.capture(id)));
      }
    });
    const snapshot = snapshotCopy({ files: this.#current.files, records }, this.#port);
    // SAFETY: The private WeakMap registers this opaque checkpoint before it is returned.
    const checkpoint = Object.freeze({}) as PreparedWidgetGraphCheckpoint<Record>;
    this.#checkpoints.set(checkpoint, { snapshot, source: this.#current });
    return checkpoint;
  }

  replace(
    target: PreparedWidgetGraphSnapshot<Record> | PreparedWidgetGraphCheckpoint<Record>,
    signal?: AbortSignal,
  ): Promise<PreparedWidgetGraphReplacement<Record>> {
    if (signal?.aborted) {
      return Promise.reject(abortReason(signal, "Prepared AnyWidget graph replacement aborted"));
    }
    if (this.#disposed) {
      return Promise.resolve(settledReplacement(false, false, () => undefined));
    }
    this.#requireIdle();
    const controller = new AbortController();
    const abort = () => {
      if (signal !== undefined) {
        controller.abort(abortReason(signal, "Prepared AnyWidget graph replacement aborted"));
      }
    };
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
    this.#active = controller;
    const operation = this.#replace(target, controller.signal);
    this.#operation = operation;
    return operation.finally(() => {
      signal?.removeEventListener("abort", abort);
      if (this.#active === controller) this.#active = undefined;
      if (this.#operation === operation) this.#operation = undefined;
    });
  }

  dispose(): Promise<void> {
    this.#disposal ??= this.#dispose();
    return this.#disposal;
  }

  async #replace(
    target: PreparedWidgetGraphSnapshot<Record> | PreparedWidgetGraphCheckpoint<Record>,
    signal: AbortSignal,
  ): Promise<PreparedWidgetGraphReplacement<Record>> {
    const checkpoint = this.#checkpoints.get(target);
    // SAFETY: Every opaque checkpoint resolves in the private WeakMap while other values are snapshots.
    const value =
      checkpoint === undefined
        ? (target as PreparedWidgetGraphSnapshot<Record>)
        : checkpoint.snapshot;
    const next = snapshotCopy(value, this.#port);
    const adopt = checkpoint?.source ?? next;
    const previous = this.#current;
    const changes = plannedChanges(previous, next, this.#port);
    const mutated =
      !sameFiles(previous.files, next.files) ||
      changes.additions.length > 0 ||
      changes.stableUpdates.length > 0 ||
      changes.replacements.length > 0 ||
      changes.removals.length > 0;
    if (!mutated) {
      return this.#replacement(false, false, () => {
        this.#current = adopt;
        return adopt;
      });
    }
    return this.#stage(previous, next, adopt, changes, signal);
  }

  async #stage(
    previous: PreparedWidgetGraphSnapshot<Record>,
    next: PreparedWidgetGraphSnapshot<Record>,
    adopt: PreparedWidgetGraphSnapshot<Record>,
    changes: GraphChanges<Record>,
    signal: AbortSignal,
  ): Promise<PreparedWidgetGraphReplacement<Record>> {
    this.#port.setFiles({ ...previous.files, ...next.files });
    try {
      await this.#preflight(changes, signal);
    } catch (error) {
      this.#port.setFiles(previous.files);
      throw error;
    }
    let live: LiveGraphState<LiveState>;
    try {
      live = captureStates(changes, this.#port);
    } catch (error) {
      this.#port.setFiles(previous.files);
      throw error;
    }
    const added = new Set<string>();
    const replaced = new Set<string>();
    const removed = new Set<string>();
    const rollback = () => this.#rollback(previous, changes, live, added, replaced, removed);
    let remount = false;
    try {
      await eachSequential(changes.additions, async (record) => {
        signal.throwIfAborted();
        added.add(this.#port.id(record));
        await this.#port.replay(record, signal);
      });
      await eachSequential(changes.stableUpdates, async (record) => {
        signal.throwIfAborted();
        await this.#port.replay(record, signal);
      });
      await eachSequential(changes.replacements, async (record) => {
        signal.throwIfAborted();
        const id = this.#port.id(record);
        const state = live.replacements.get(id)!;
        replaced.add(id);
        remount = true;
        await this.#port.close(id);
        await this.#port.replay(this.#port.merge(record, state), signal);
      });
      signal.throwIfAborted();
    } catch (error) {
      const failure = graphFailure(error);
      try {
        await rollback();
      } catch (rollbackError) {
        throw new PreparedWidgetGraphReplacementError(
          new AggregateError(
            [failure, graphFailure(rollbackError)],
            "Prepared AnyWidget graph replacement and rollback failed",
          ),
        );
      }
      if (remount) throw new PreparedWidgetGraphReplacementError(failure);
      throw failure;
    }
    return this.#replacement(true, changes.replacements.length > 0, async (commit) => {
      if (!commit) {
        await rollback();
        return undefined;
      }
      await eachSequential(changes.removals, async (record) => {
        const id = this.#port.id(record);
        removed.add(id);
        await this.#port.close(id);
      });
      this.#port.setFiles(next.files);
      this.#current = adopt;
      return adopt;
    });
  }

  async #preflight(changes: GraphChanges<Record>, signal: AbortSignal): Promise<void> {
    await eachSequential(
      [...changes.additions, ...changes.stableUpdates, ...changes.replacements],
      async (record) => {
        signal.throwIfAborted();
        await this.#port.validate?.(record, signal);
      },
    );
    await eachSequential([...changes.additions, ...changes.replacements], async (record) => {
      signal.throwIfAborted();
      await this.#port.preflight?.(record, signal);
    });
    signal.throwIfAborted();
  }

  async #rollback(
    previous: PreparedWidgetGraphSnapshot<Record>,
    changes: GraphChanges<Record>,
    live: LiveGraphState<LiveState>,
    added: ReadonlySet<string>,
    replaced: ReadonlySet<string>,
    removed: ReadonlySet<string>,
  ): Promise<void> {
    this.#port.setFiles(previous.files);
    const errors: Error[] = [];
    await eachSequential(added, (id) => attempt(errors, () => this.#port.close(id)));
    await eachSequential(live.stable, ([id, state]) =>
      attempt(errors, () => this.#port.restore(id, state)),
    );
    await this.#replayPrevious(errors, previous, live.replacements, replaced);
    await this.#replayPrevious(errors, previous, live.removals, removed);
    throwCleanup(errors, "Prepared AnyWidget graph rollback failed");
  }

  async #replayPrevious(
    errors: Error[],
    previous: PreparedWidgetGraphSnapshot<Record>,
    states: ReadonlyMap<string, LiveState>,
    ids: ReadonlySet<string>,
  ): Promise<void> {
    await eachSequential(ids, async (id) => {
      const record = previous.records.get(id);
      const state = states.get(id);
      if (record === undefined || state === undefined) return;
      await attempt(errors, () => this.#port.close(id));
      await attempt(errors, () => this.#port.replay(this.#port.merge(record, state)));
    });
  }

  #replacement(
    mutated: boolean,
    remount: boolean,
    settle: (
      commit: boolean,
    ) =>
      | PreparedWidgetGraphSnapshot<Record>
      | undefined
      | Promise<PreparedWidgetGraphSnapshot<Record> | undefined>,
  ): PreparedWidgetGraphReplacement<Record> {
    const replacement = settledReplacement(mutated, remount, async (commit) => {
      let settled = false;
      try {
        const result = await settle(commit);
        settled = true;
        return result;
      } finally {
        if (settled && this.#pending === replacement) this.#pending = undefined;
      }
    });
    this.#pending = replacement;
    return replacement;
  }

  #requireIdle(): void {
    if (this.#disposed) throw new Error("Prepared AnyWidget graph is disposed");
    if (this.#operation !== undefined || this.#pending !== undefined) {
      throw new Error("A prepared AnyWidget graph replacement is already active");
    }
  }

  async #dispose(): Promise<void> {
    this.#disposed = true;
    this.#active?.abort(new DOMException("Prepared AnyWidget graph disposed", "AbortError"));
    const errors: Error[] = [];
    if (this.#operation !== undefined) await attempt(errors, () => this.#operation?.then(() => {}));
    if (this.#pending !== undefined) await attempt(errors, () => this.#pending?.rollback());
    await eachSequential(this.#current.records, ([id, record]) =>
      this.#port.active(record) ? attempt(errors, () => this.#port.close(id)) : undefined,
    );
    await attempt(errors, () => this.#port.setFiles({}));
    this.#current = emptyGraph();
    throwCleanup(errors, "Prepared AnyWidget graph disposal failed");
  }
}
