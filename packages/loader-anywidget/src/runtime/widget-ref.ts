export function parseWidgetRef(value: unknown): string {
  if (typeof value === "string" && value.startsWith("anywidget:")) {
    const modelId = value.slice("anywidget:".length);
    if (modelId.length > 0) return modelId;
  }
  throw new Error(`Invalid AnyWidget model reference: ${JSON.stringify(value)}.`);
}
