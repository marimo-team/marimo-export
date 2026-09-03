import { portableJsonObject } from "@marimo-team/portable-json";
import type { JsonObject } from "@marimo-team/portable-json";

import type { ExportState } from "../types.js";
import { isNotebookExportError } from "../types.js";
import { isPreparedAbort } from "./cancellation.js";
import { samePreparedInputs } from "./control.js";
import type { PreparedPublication } from "./manifest.js";

export const mergePreparedInputs = (base: JsonObject, patch: JsonObject): JsonObject =>
  portableJsonObject({ ...base, ...patch }, "prepared state patch");

export const withPreparedState = (
  publication: PreparedPublication,
  state: ExportState,
): PreparedPublication => Object.freeze({ ...publication, state });

export const selectPendingPublication = (
  publication: PreparedPublication,
  pendingInputs: JsonObject | undefined,
): PreparedPublication => {
  if (pendingInputs === undefined) {
    return publication;
  }
  try {
    return withPreparedState(publication, publication.notebookExport.resolve(pendingInputs));
  } catch (error) {
    if (!isPreparedStateUnavailable(error)) {
      throw error;
    }
    return publication;
  }
};

export const samePreparedInputContract = (
  left: PreparedPublication,
  right: PreparedPublication,
): boolean => {
  const leftNames = left.notebookExport.inputNames;
  const rightNames = new Set(right.notebookExport.inputNames);
  return leftNames.length === rightNames.size && leftNames.every((name) => rightNames.has(name));
};

export const pendingInputsForPublication = (
  current: PreparedPublication | undefined,
  next: PreparedPublication,
  pending: JsonObject | undefined,
): JsonObject | undefined =>
  pending !== undefined && current !== undefined && !samePreparedInputContract(current, next)
    ? undefined
    : pending;

export const isPreparedStateUnavailable = <Value>(value: Value): boolean =>
  isNotebookExportError(value) && value.code === "state_unavailable";

export const isPreparedStatePendingFailure = <Value>(value: Value): boolean => {
  if (!isNotebookExportError(value)) {
    return false;
  }
  if (value.code === "state_unavailable") {
    return true;
  }
  return (
    value.code === "read_failed" &&
    value.details !== undefined &&
    Object.hasOwn(value.details, "status") &&
    value.details.status === 404
  );
};

export const pendingInputsAfterFailure = <Failure>(
  pending: JsonObject | undefined,
  requested: JsonObject,
  error: Failure,
  callerAborted: boolean,
): JsonObject | undefined => {
  if (pending === undefined || !samePreparedInputs(pending, requested)) {
    return pending;
  }
  if (isPreparedAbort(error)) {
    return callerAborted ? undefined : pending;
  }
  return isPreparedStatePendingFailure(error) ? pending : undefined;
};

export const preservePreparedSelection = (
  current: PreparedPublication | undefined,
  next: PreparedPublication,
): PreparedPublication => {
  if (
    current === undefined ||
    current.state.fingerprint === current.manifest.stateFingerprint ||
    !samePreparedInputContract(current, next)
  ) {
    return next;
  }
  try {
    return withPreparedState(next, next.notebookExport.resolve(current.state.inputs));
  } catch (error) {
    if (
      isNotebookExportError(error) &&
      (error.code === "state_input_invalid" || error.code === "state_unavailable")
    ) {
      return next;
    }
    throw error;
  }
};
