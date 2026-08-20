import { portableJsonObject } from "@marimo-team/portable-json";
import { describe, expect, it } from "vite-plus/test";

import { preparedControlInputPatch, samePreparedInputs } from "../src/prepared/index.js";

describe("prepared control bindings", () => {
  it("patches roots and nested object paths without mutating current inputs", () => {
    const inputs = { filters: { region: "emea", year: 2025 } };

    expect(
      preparedControlInputPatch(inputs, { input: "filters", path: [] }, { region: "apac" }),
    ).toEqual({ filters: { region: "apac" } });
    expect(
      preparedControlInputPatch(
        inputs,
        { input: "filters", path: [{ kind: "key", value: "region" }] },
        "apac",
      ),
    ).toEqual({ filters: { region: "apac", year: 2025 } });
    expect(inputs).toEqual({ filters: { region: "emea", year: 2025 } });
  });

  it("patches array indexes and leaves element-owned controls to the application", () => {
    expect(
      preparedControlInputPatch(
        { selection: ["first", "second"] },
        { input: "selection", path: [{ kind: "index", value: 1 }] },
        "updated",
      ),
    ).toEqual({ selection: ["first", "updated"] });
    expect(
      preparedControlInputPatch(
        { filters: { region: "emea" } },
        {
          input: "filters",
          path: [{ kind: "element" }, { kind: "key", value: "region" }],
        },
        "apac",
      ),
    ).toBeUndefined();
  });

  it("rejects paths outside the current input shape", () => {
    expect(() =>
      preparedControlInputPatch(
        { filters: { region: "emea" } },
        { input: "filters", path: [{ kind: "key", value: "missing" }] },
        "apac",
      ),
    ).toThrow(expect.objectContaining({ code: "manifest_invalid" }));
  });

  it("compares portable inputs structurally and independently of key order", () => {
    expect(
      samePreparedInputs(
        { filters: { detail: "ready", region: ["Europe"] }, scale: 3 },
        { scale: 3, filters: { region: ["Europe"], detail: "ready" } },
      ),
    ).toBe(true);
    expect(
      samePreparedInputs({ selection: ["first", "second"] }, { selection: ["second", "first"] }),
    ).toBe(false);
  });

  it("patches reserved keys without changing object prototypes", () => {
    const inputs = portableJsonObject(
      JSON.parse('{"__proto__":{"nested":{"__proto__":"before"}}}'),
    );
    const patched = preparedControlInputPatch(
      inputs,
      {
        input: "__proto__",
        path: [
          { kind: "key", value: "nested" },
          { kind: "key", value: "__proto__" },
        ],
      },
      "after",
    );
    const root = portableJsonObject(patched?.__proto__);
    const nested = portableJsonObject(root.nested);

    expect(Object.hasOwn(patched ?? {}, "__proto__")).toBe(true);
    expect(Object.hasOwn(nested, "__proto__")).toBe(true);
    expect(nested.__proto__).toBe("after");
    expect(Object.getPrototypeOf({})).toBe(Object.prototype);
  });
});
