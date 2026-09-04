import fixtures from "../../../tests/fixtures/portable-json.json";
import { describe, expect, test } from "vite-plus/test";

import {
  MAX_JSON_DEPTH,
  MAX_JSON_VALUES,
  parsePortableJson,
  parseStrictJson,
  portableJsonObject,
  portableJsonValue,
} from "../src/index.js";
import type { JsonValue } from "../src/index.js";

describe("portable JSON conversion", () => {
  test("detaches and freezes values while preserving shared aliases", () => {
    const shared = { count: 2 };
    const source = { first: shared, second: shared, values: [shared] };

    const value = portableJsonObject(source);
    shared.count = 3;

    expect(value).toEqual({ first: { count: 2 }, second: { count: 2 }, values: [{ count: 2 }] });
    expect(value.first).not.toBe(value.second);
    expect(Object.isFrozen(value)).toBe(true);
    expect(Object.isFrozen(value.first)).toBe(true);
  });

  test("preserves reserved object keys", () => {
    const value = portableJsonObject(
      JSON.parse('{"__proto__":{"nested":{"__proto__":"kept"}},"safe":true}'),
    );

    expect(Object.hasOwn(value, "__proto__")).toBe(true);
    const root = portableJsonObject(value.__proto__);
    const nested = portableJsonObject(root.nested);
    expect(Object.hasOwn(nested, "__proto__")).toBe(true);
    expect(nested.__proto__).toBe("kept");
    expect(Object.hasOwn(Object.prototype, "nested")).toBe(false);
  });

  test("rejects active container cycles", () => {
    const self: Cycle = {};
    self.child = self;
    const left: Cycle = {};
    const right: Cycle = { child: left };
    left.child = right;

    expect(() => portableJsonValue(self)).toThrow("cyclic container");
    expect(() => portableJsonValue(left)).toThrow("cyclic container");
  });

  test("enforces depth and value limits", () => {
    let deep: JsonValue = null;
    for (let depth = 0; depth <= MAX_JSON_DEPTH; depth += 1) {
      deep = Object.fromEntries([["child", deep]]);
    }
    const oversized = Array.from({ length: MAX_JSON_VALUES }, () => null);

    expect(() => portableJsonValue(deep)).toThrow("maximum JSON nesting depth");
    expect(() => portableJsonValue(oversized)).toThrow("maximum JSON value count");
    expect(() => parseStrictJson('{"first":0,"second":1}', 4)).toThrow("maximum value count");
  });

  test("rejects sparse arrays before allocating their output", () => {
    const sparse = Array(2);
    sparse[1] = "ready";
    const hugeSparse: unknown[] = [];
    hugeSparse.length = MAX_JSON_VALUES + 1;
    const overridden = Object.assign([1, 2], {
      map: () => {
        throw new Error("array map must not run");
      },
    });

    expect(() => portableJsonValue(sparse)).toThrow("must be present");
    expect(() => portableJsonValue(hugeSparse)).toThrow("maximum JSON value count");
    expect(portableJsonValue(overridden)).toEqual([1, 2]);
  });

  test("rejects incompatible numbers and Unicode strings", () => {
    for (const value of [Number.NaN, Number.POSITIVE_INFINITY, 2 ** 53]) {
      expect(() => portableJsonValue(value)).toThrow();
    }
    expect(() => portableJsonValue("\ud800")).toThrow("Unicode scalar values");
    expect(() => portableJsonObject(Object.fromEntries([["\ud800", 1]]))).toThrow(
      "Unicode scalar values",
    );
    expect(Object.is(portableJsonValue(-0), 0)).toBe(true);
  });

  test("rejects boxed primitives", () => {
    for (const value of [new Boolean(true), new Number(1), new String("ready")]) {
      expect(() => portableJsonValue(value)).toThrow("must be JSON-compatible");
    }
  });
});

describe("portable JSON parsing", () => {
  test.each(fixtures.valid)(
    "matches the Python contract for $name",
    ({ source, expected_source }) => {
      const value = parsePortableJson(source);
      expect(value).toEqual(parsePortableJson(expected_source));
      expect(Object.isFrozen(value)).toBe(true);
    },
  );

  test.each(fixtures.invalid)("rejects $name", ({ source }) => {
    expect(() => parsePortableJson(source)).toThrow();
  });
});

interface Cycle {
  child?: Cycle;
}
