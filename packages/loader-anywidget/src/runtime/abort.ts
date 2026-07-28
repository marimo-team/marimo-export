export function combineAbortSignals(signals: readonly AbortSignal[]): AbortSignal {
  const sources = [...new Set(signals)];
  if (typeof AbortSignal.any === "function") return AbortSignal.any(sources);

  const controller = new AbortController();
  const listeners = new Map<AbortSignal, () => void>();
  const settle = (signal: AbortSignal) => {
    for (const [source, listener] of listeners) source.removeEventListener("abort", listener);
    listeners.clear();
    controller.abort(signal.reason);
  };
  for (const signal of sources) {
    if (signal.aborted) {
      settle(signal);
      break;
    }
    const listener = () => settle(signal);
    listeners.set(signal, listener);
    signal.addEventListener("abort", listener, { once: true });
  }
  return controller.signal;
}

export async function raceAbort<T>(
  task: Promise<T>,
  signal: AbortSignal,
  message: string,
): Promise<T> {
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

export function abortError(message: string): Error {
  return Object.assign(new Error(message), { name: "AbortError" });
}

export function abortReason(signal: AbortSignal, message: string): Error {
  return signal.reason instanceof Error ? signal.reason : abortError(message);
}

export function throwIfAborted(signal: AbortSignal, message: string): void {
  if (signal.aborted) throw abortReason(signal, message);
}
