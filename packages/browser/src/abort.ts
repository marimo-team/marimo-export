export function isAbortError(error: unknown): boolean {
  if ((typeof error !== "object" && typeof error !== "function") || error === null) return false;
  try {
    return Reflect.get(error, "name") === "AbortError";
  } catch {
    return false;
  }
}
