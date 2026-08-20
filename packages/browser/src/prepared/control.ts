import { portableJsonObject, portableJsonValue } from "@marimo-team/portable-json";
import type { JsonObject, JsonValue } from "@marimo-team/portable-json";

import type { ControlBinding } from "../types.js";
import { PreparedExportError } from "./errors.js";

const sameJsonValue = (left: JsonValue, right: JsonValue): boolean => {
  if (Object.is(left, right)) {
    return true;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => sameJsonValue(value, right[index]!))
    );
  }
  if (!isJsonObject(left) || !isJsonObject(right)) {
    return false;
  }
  const leftKeys = Object.keys(left);
  return (
    leftKeys.length === Object.keys(right).length &&
    leftKeys.every((key) => Object.hasOwn(right, key) && sameJsonValue(left[key]!, right[key]!))
  );
};

export const samePreparedInputs = (left: JsonObject, right: JsonObject): boolean =>
  sameJsonValue(left, right);

const invalidBinding = (binding: ControlBinding): PreparedExportError =>
  new PreparedExportError(
    "manifest_invalid",
    `Control binding for input ${JSON.stringify(binding.input)} does not match the current state.`,
  );

const replaceControlValue = (
  current: JsonValue,
  binding: ControlBinding,
  offset: number,
  next: JsonValue,
): JsonValue => {
  const step = binding.path[offset];
  if (step === undefined) {
    return next;
  }
  if (step.kind === "element") {
    throw invalidBinding(binding);
  }
  if (step.kind === "index" && Array.isArray(current)) {
    if (step.value >= current.length) {
      throw invalidBinding(binding);
    }
    const replaced = [...current];
    replaced[step.value] = replaceControlValue(current[step.value]!, binding, offset + 1, next);
    return Object.freeze(replaced);
  }
  if (!isJsonObject(current)) {
    throw invalidBinding(binding);
  }
  const key = step.kind === "index" ? String(step.value) : step.value;
  if (!Object.hasOwn(current, key)) {
    throw invalidBinding(binding);
  }
  return portableJsonObject({
    ...current,
    [key]: replaceControlValue(current[key]!, binding, offset + 1, next),
  });
};

const isJsonObject = (value: JsonValue): value is JsonObject =>
  value !== null && typeof value === "object" && !Array.isArray(value);

export const preparedControlInputPatch = (
  inputs: JsonObject,
  binding: ControlBinding,
  value: JsonValue,
): JsonObject | undefined => {
  if (binding.path.some((step) => step.kind === "element")) {
    return undefined;
  }
  if (!Object.hasOwn(inputs, binding.input)) {
    throw invalidBinding(binding);
  }
  return portableJsonObject({
    [binding.input]: replaceControlValue(
      inputs[binding.input]!,
      binding,
      0,
      portableJsonValue(value, "control value"),
    ),
  });
};
