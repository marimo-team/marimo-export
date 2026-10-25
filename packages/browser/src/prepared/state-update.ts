import type { JsonObject, JsonValue } from "@marimo-team/portable-json";

import { preparedControlInputPatch } from "./control.js";
import type { PreparedPublication } from "./manifest.js";
import { queryInputPatch } from "./query.js";

export const preparedControlPatch = (
  publication: PreparedPublication,
  inputs: JsonObject,
  objectId: string,
  value: JsonValue,
): JsonObject | undefined => {
  const bindings = publication.notebookExport.controlBindings;
  if (!Object.hasOwn(bindings, objectId)) {
    return undefined;
  }
  return preparedControlInputPatch(inputs, bindings[objectId]!, value);
};

export const preparedQueryPatch = (
  publication: PreparedPublication,
  query: string,
): JsonObject | undefined => {
  const patch = queryInputPatch(publication.notebookExport, query);
  return patch === undefined ? undefined : publication.state.resolve(patch).inputs;
};
