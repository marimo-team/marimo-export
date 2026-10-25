import type { JsonObject, JsonValue } from "@marimo-team/portable-json";

import type { ExportState, NotebookExport } from "../types.js";
import { isStringValue } from "../value-types.js";
import { PreparedExportError } from "./errors.js";

const jsonKey = (value: JsonValue): string => JSON.stringify(value);
const queryText = (value: JsonValue): string =>
  isStringValue(value) ? value : JSON.stringify(value);

const searchParams = (query: string): URLSearchParams =>
  new URLSearchParams(query.startsWith("?") ? query.slice(1) : query);

interface InputDomain {
  readonly size: number;
  readonly byText: ReadonlyMap<string, readonly JsonValue[]>;
}

// Notebook exports are immutable, so each input domain is computed once per export.
const domains = new WeakMap<NotebookExport, Map<string, InputDomain>>();

const inputDomain = (notebookExport: NotebookExport, name: string): InputDomain => {
  let exportDomains = domains.get(notebookExport);
  if (exportDomains === undefined) {
    exportDomains = new Map();
    domains.set(notebookExport, exportDomains);
  }
  const cached = exportDomains.get(name);
  if (cached !== undefined) {
    return cached;
  }
  const values = new Map<string, JsonValue>();
  for (const state of notebookExport.states()) {
    if (Object.hasOwn(state.inputs, name)) {
      const value = state.inputs[name]!;
      values.set(jsonKey(value), value);
    }
  }
  const byText = new Map<string, JsonValue[]>();
  for (const value of values.values()) {
    const text = queryText(value);
    byText.set(text, [...(byText.get(text) ?? []), value]);
  }
  const domain = { size: values.size, byText };
  exportDomains.set(name, domain);
  return domain;
};

const textMatches = (domain: InputDomain, text: string): readonly JsonValue[] =>
  domain.byText.get(text) ?? [];

const ambiguous = (name: string): PreparedExportError =>
  new PreparedExportError(
    "query_ambiguous",
    `Query input ${JSON.stringify(name)} matches more than one exported value.`,
  );

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
  const matches = textMatches(inputDomain(notebookExport, name), values[0]!);
  if (matches.length === 0) {
    throw new PreparedExportError(
      "query_miss",
      `Query input ${JSON.stringify(name)} has no exported value matching ${JSON.stringify(values[0])}.`,
    );
  }
  if (matches.length > 1) {
    throw ambiguous(name);
  }
  return matches[0]!;
};

export const queryInputPatch = (
  notebookExport: NotebookExport,
  query: string,
): JsonObject | undefined => {
  const search = searchParams(query);
  const patch: Array<readonly [string, JsonValue]> = [];
  for (const name of notebookExport.inputNames) {
    if (search.has(name)) {
      patch.push([name, queryInput(notebookExport, name, search.getAll(name))]);
    }
  }
  return patch.length === 0 ? undefined : Object.fromEntries(patch);
};

export const resolvePreparedQuery = (current: ExportState, query: string): ExportState => {
  const patch = queryInputPatch(current.notebookExport, query);
  return patch === undefined ? current : current.resolve(patch);
};

export const formatPreparedQuery = (state: ExportState, query = ""): string => {
  const { notebookExport } = state;
  const search = searchParams(query);
  for (const name of notebookExport.inputNames) {
    search.delete(name);
  }
  for (const name of notebookExport.inputNames) {
    const domain = inputDomain(notebookExport, name);
    if (domain.size < 2) continue;
    const text = queryText(state.inputs[name]!);
    if (textMatches(domain, text).length > 1) {
      throw ambiguous(name);
    }
    search.append(name, text);
  }
  const text = search.toString();
  return text.length === 0 ? "" : `?${text}`;
};
