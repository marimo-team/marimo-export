import { isPropertyOwner } from "./value-types.js";

export function isAbortError<Value>(cause: Value): boolean {
  if (!isPropertyOwner(cause) || !("name" in cause)) return false;
  try {
    return cause.name === "AbortError";
  } catch {
    return false;
  }
}
