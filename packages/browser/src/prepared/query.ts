import type { JsonValue } from "@marimo-team/portable-json";

import type { ExportState, NotebookExport } from "../types.js";
import { isStringValue } from "../value-types.js";
import { PreparedExportError } from "./errors.js";

const jsonKey = (value: JsonValue): string => JSON.stringify(value);
const queryText = (value: JsonValue): string =>
  isStringValue(value) ? value : JSON.stringify(value);

const inputDomain = (notebookExport: NotebookExport, name: string): readonly JsonValue[] => {
  const values = new Map<string, JsonValue>();
  for (const state of notebookExport.states()) {
    if (Object.hasOwn(state.inputs, name)) {
      const value = state.inputs[name]!;
      values.set(jsonKey(value), value);
    }
  }
  return Array.from(values.values());
};

const queryInput = (
  notebookExport: NotebookExport,
  name: string,
  values: readonly string[],
): JsonValue => {
  if (values.length !== 1) {
    throw new PreparedExportError(
      "query_miss",
      `Query input ${JSON.stringify(name)} must have exactly one value.`,
    );
  }
  const matches = inputDomain(notebookExport, name).filter(
    (candidate) => queryText(candidate) === values[0],
  );
  if (matches.length === 0) {
    throw new PreparedExportError(
      "query_miss",
      `Query input ${JSON.stringify(name)} has no exported value matching ${JSON.stringify(values[0])}.`,
    );
  }
  if (matches.length > 1) {
    throw new PreparedExportError(
      "query_ambiguous",
      `Query input ${JSON.stringify(name)} matches more than one exported value.`,
    );
  }
  return matches[0]!;
};

export const resolvePreparedQuerySelection = (
  notebookExport: NotebookExport,
  current: ExportState,
  query: string,
): ExportState | undefined => {
  const search = new URLSearchParams(query.startsWith("?") ? query.slice(1) : query);
  const patch: Array<readonly [string, JsonValue]> = [];
  for (const name of notebookExport.inputNames) {
    if (search.has(name)) {
      patch.push([name, queryInput(notebookExport, name, search.getAll(name))]);
    }
  }
  return patch.length === 0 ? undefined : current.resolve(Object.fromEntries(patch));
};

export const resolvePreparedQueryState = (
  notebookExport: NotebookExport,
  current: ExportState,
  query: string,
): ExportState => resolvePreparedQuerySelection(notebookExport, current, query) ?? current;
