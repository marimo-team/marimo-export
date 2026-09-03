import { isStringValue } from "./value-types.js";

export function parseWidgetRef<Value>(value: Value): string {
  if (isStringValue(value) && value.startsWith("anywidget:")) {
    const modelId = value.slice("anywidget:".length);
    if (modelId.length > 0) return modelId;
  }
  throw new Error(`Invalid AnyWidget model reference: ${JSON.stringify(value)}.`);
}
