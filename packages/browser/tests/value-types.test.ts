import { runInNewContext } from "node:vm";
import { describe, expect, test } from "vite-plus/test";

import {
  isBigIntValue,
  isBooleanValue,
  isCallableValue,
  isNumberValue,
  isStringValue,
} from "../src/value-types.js";

describe("runtime value guards", () => {
  test("distinguish primitive values from boxed objects", () => {
    expect(isStringValue("ready")).toBe(true);
    expect(isStringValue(Object("ready"))).toBe(false);
    expect(isNumberValue(Number.NaN)).toBe(true);
    expect(isNumberValue(Object(1))).toBe(false);
    expect(isBooleanValue(false)).toBe(true);
    expect(isBooleanValue(Object(false))).toBe(false);
    expect(isBigIntValue(1n)).toBe(true);
    expect(isBigIntValue(Object(1n))).toBe(false);
  });

  test("recognize callables across JavaScript realms", () => {
    const foreignFunction = runInNewContext("(value) => value");

    expect(isCallableValue(foreignFunction)).toBe(true);
    expect(isCallableValue({ call: foreignFunction })).toBe(false);
  });
});
