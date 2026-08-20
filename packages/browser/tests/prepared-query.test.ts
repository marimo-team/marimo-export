import { describe, expect, it } from "vite-plus/test";

import {
  PreparedExportError,
  resolvePreparedQuerySelection,
  resolvePreparedQueryState,
} from "../src/prepared/index.js";
import { preparedExportFixture } from "./prepared-fixture.js";

describe("prepared query selection", () => {
  it("matches query text against declared input domains", () => {
    const notebookExport = preparedExportFixture({
      inputs: [
        { count: 1, mode: "baseline" },
        { count: 2, mode: "baseline" },
        { count: 2, mode: "alternate" },
      ],
    });
    const current = notebookExport.resolve({ count: 1, mode: "baseline" });

    const selected = resolvePreparedQueryState(notebookExport, current, "?count=2&mode=alternate");

    expect(selected.inputs).toEqual({ count: 2, mode: "alternate" });
  });

  it("ignores keys outside the export input contract", () => {
    const notebookExport = preparedExportFixture({ inputs: [{ mode: "baseline" }] });
    const current = notebookExport.resolve({ mode: "baseline" });

    expect(
      resolvePreparedQuerySelection(notebookExport, current, "?runtime=zero-python"),
    ).toBeUndefined();
  });

  it("reports missing, repeated, and ambiguous query values", () => {
    const ordinary = preparedExportFixture({ inputs: [{ mode: "baseline" }] });
    const current = ordinary.resolve({ mode: "baseline" });
    expect(() => resolvePreparedQueryState(ordinary, current, "?mode=missing")).toThrow(
      expect.objectContaining({ code: "query_miss" }),
    );
    expect(() =>
      resolvePreparedQueryState(ordinary, current, "?mode=baseline&mode=baseline"),
    ).toThrow(PreparedExportError);

    const collision = preparedExportFixture({ inputs: [{ value: "1" }, { value: 1 }] });
    expect(() =>
      resolvePreparedQueryState(collision, collision.resolve({ value: "1" }), "?value=1"),
    ).toThrow(expect.objectContaining({ code: "query_ambiguous" }));
  });

  it("selects reserved and inherited input names as own values", () => {
    const notebookExport = preparedExportFixture({
      inputs: [
        JSON.parse('{"__proto__":"baseline","toString":"baseline"}'),
        JSON.parse('{"__proto__":"alternate","toString":"alternate"}'),
      ],
    });
    const current = notebookExport.states()[0]!;

    const selected = resolvePreparedQueryState(
      notebookExport,
      current,
      "?__proto__=alternate&toString=alternate",
    );

    expect(Object.hasOwn(selected.inputs, "__proto__")).toBe(true);
    expect(Object.hasOwn(selected.inputs, "toString")).toBe(true);
    expect(selected.inputs.__proto__).toBe("alternate");
    expect(selected.inputs.toString).toBe("alternate");
  });
});
