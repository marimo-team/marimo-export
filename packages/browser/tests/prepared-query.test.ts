import { describe, expect, it } from "vite-plus/test";

import { openExport } from "../src/index.js";
import {
  PreparedExportError,
  formatPreparedQuery,
  isPreparedExportError,
  resolvePreparedQuery,
} from "../src/prepared/index.js";
import { exportFixture } from "./fixture.js";
import { preparedExportFixture } from "./prepared-fixture.js";

describe("prepared query resolution", () => {
  it("matches query text against declared input domains", () => {
    const notebookExport = preparedExportFixture({
      inputs: [
        { count: 1, mode: "baseline" },
        { count: 2, mode: "baseline" },
        { count: 2, mode: "alternate" },
      ],
    });
    const current = notebookExport.resolve({ count: 1, mode: "baseline" });

    const selected = resolvePreparedQuery(current, "?count=2&mode=alternate");

    expect(selected.inputs).toEqual({ count: 2, mode: "alternate" });
  });

  it("keeps current values for inputs absent from the query", () => {
    const notebookExport = preparedExportFixture({
      inputs: [
        { count: 1, mode: "baseline" },
        { count: 2, mode: "baseline" },
        { count: 1, mode: "alternate" },
        { count: 2, mode: "alternate" },
      ],
    });
    const current = notebookExport.resolve({ count: 2, mode: "alternate" });

    expect(resolvePreparedQuery(current, "count=1").inputs).toEqual({
      count: 1,
      mode: "alternate",
    });
  });

  it("keeps the current state for keys outside the export input contract", () => {
    const notebookExport = preparedExportFixture({ inputs: [{ mode: "baseline" }] });
    const current = notebookExport.resolve({ mode: "baseline" });

    expect(resolvePreparedQuery(current, "?runtime=zero-python")).toBe(current);
  });

  it("reports missing, repeated, and ambiguous query values", () => {
    const ordinary = preparedExportFixture({ inputs: [{ mode: "baseline" }] });
    const current = ordinary.resolve({ mode: "baseline" });
    let missing: unknown;
    try {
      resolvePreparedQuery(current, "?mode=missing");
    } catch (error) {
      missing = error;
    }
    expect(isPreparedExportError(missing)).toBe(true);
    expect(missing).toMatchObject({ code: "query_miss" });
    expect(() => resolvePreparedQuery(current, "?mode=baseline&mode=baseline")).toThrow(
      PreparedExportError,
    );

    const collision = preparedExportFixture({ inputs: [{ value: "1" }, { value: 1 }] });
    expect(() => resolvePreparedQuery(collision.resolve({ value: "1" }), "?value=1")).toThrow(
      expect.objectContaining({ code: "query_ambiguous" }),
    );
  });

  it("selects reserved and inherited input names as own values", () => {
    const notebookExport = preparedExportFixture({
      inputs: [
        JSON.parse('{"__proto__":"baseline","toString":"baseline"}'),
        JSON.parse('{"__proto__":"alternate","toString":"alternate"}'),
      ],
    });
    const current = notebookExport.states()[0]!;

    const selected = resolvePreparedQuery(current, "?__proto__=alternate&toString=alternate");

    expect(Object.hasOwn(selected.inputs, "__proto__")).toBe(true);
    expect(Object.hasOwn(selected.inputs, "toString")).toBe(true);
    expect(selected.inputs.__proto__).toBe("alternate");
    expect(selected.inputs.toString).toBe("alternate");
  });
});

describe("prepared query formatting", () => {
  const widget = { rows: [{ close: 221.8, symbol: "AAPL" }] };
  const mixed = preparedExportFixture({
    inputs: [
      { flag: false, label: "plain", limit: 9, symbols: ["AAPL", "MSFT"], widget },
      { flag: true, label: "9", limit: 9, symbols: [], widget },
      { flag: false, label: "true", limit: 12.5, symbols: ["MSFT"], widget },
      { flag: true, label: "", limit: -3, symbols: ["AAPL", "MSFT"], widget },
      { flag: false, label: "Zürich 東京 & co=1", limit: 9, symbols: [["nested"]], widget },
      { flag: true, label: "null", limit: 0, symbols: [{ ticker: "CRWV", weight: 0.5 }], widget },
    ],
  });

  it("round-trips every state from every current state", () => {
    for (const state of mixed.states()) {
      const query = formatPreparedQuery(state);
      for (const current of mixed.states()) {
        expect(resolvePreparedQuery(current, query)).toBe(state);
      }
    }
  });

  it("round-trips the states of an opened export", async () => {
    const { fetch } = await exportFixture();
    const notebookExport = await openExport("https://example.test/stocks/", { fetch });

    for (const state of notebookExport.states()) {
      const query = formatPreparedQuery(state, "?export=./stocks/");
      expect(new URLSearchParams(query).get("export")).toBe("./stocks/");
      expect(resolvePreparedQuery(notebookExport.defaultState, query)).toBe(state);
    }
  });

  it("writes varying inputs with strings as raw text and other values as JSON text", () => {
    const state = mixed.resolve({ flag: true, label: "9", limit: 9, symbols: [], widget });

    const query = new URLSearchParams(formatPreparedQuery(state));

    expect([...query]).toEqual([
      ["flag", "true"],
      ["label", "9"],
      ["limit", "9"],
      ["symbols", "[]"],
    ]);
  });

  it("replaces input parameters and keeps application parameters", () => {
    const state = mixed.resolve({
      flag: false,
      label: "true",
      limit: 12.5,
      symbols: ["MSFT"],
      widget,
    });

    const query = formatPreparedQuery(state, "?export=./v2/&limit=9&tab=chart&widget=0&limit=0");

    expect([...new URLSearchParams(query)]).toEqual([
      ["export", "./v2/"],
      ["tab", "chart"],
      ["flag", "false"],
      ["label", "true"],
      ["limit", "12.5"],
      ["symbols", '["MSFT"]'],
    ]);
    expect(query.startsWith("?")).toBe(true);
  });

  it("rejects a value whose query text names more than one exported value", () => {
    const collision = preparedExportFixture({ inputs: [{ value: "1" }, { value: 1 }] });

    expect(() => formatPreparedQuery(collision.resolve({ value: 1 }))).toThrow(
      expect.objectContaining({ code: "query_ambiguous" }),
    );
  });
});
