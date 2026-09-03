import { isNotebookExportError } from "../types.js";

export const preparedAbortReason = <Reason>(
  reason: Reason,
  message = "Prepared export operation aborted",
): Error | DOMException => {
  if (reason instanceof Error || reason instanceof DOMException) {
    return reason;
  }
  return new DOMException(message, "AbortError");
};

export const isPreparedAbort = <Value>(value: Value): boolean =>
  (value instanceof DOMException && value.name === "AbortError") ||
  (value instanceof Error && value.name === "AbortError") ||
  (isNotebookExportError(value) && value.code === "abort");

export const throwIfPreparedAborted = (signal: AbortSignal | undefined, message?: string): void => {
  if (signal?.aborted) {
    throw preparedAbortReason(signal.reason, message);
  }
};

export const linkPreparedAbort = (
  controller: AbortController,
  signal: AbortSignal,
): (() => void) => {
  const abort = () => controller.abort(preparedAbortReason(signal.reason));
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) {
    abort();
  }
  return () => signal.removeEventListener("abort", abort);
};

export const disposePreparedOnAbort = (
  signal: AbortSignal,
  lifecycle: AbortController,
  dispose: () => Promise<void>,
): (() => void) => {
  const disposeAfterAbort = () => {
    void dispose().catch(() => {});
  };
  const abort = () => {
    lifecycle.abort(preparedAbortReason(signal.reason));
    disposeAfterAbort();
  };
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) {
    lifecycle.abort(preparedAbortReason(signal.reason));
    queueMicrotask(disposeAfterAbort);
  }
  return () => signal.removeEventListener("abort", abort);
};
