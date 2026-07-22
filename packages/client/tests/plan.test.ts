import { describe, expect, test } from "vite-plus/test";

import { validateExportPlan } from "../src/remote.js";
import { EXPORT_PLAN_SCHEMA } from "../src/remote/plan.js";

describe("export plan validation", () => {
  test("accepts a complete plan and preserves its frozen wire shape", () => {
    const plan = {
      schema: EXPORT_PLAN_SCHEMA,
      inputs: {
        symbol: { ui: "symbol_picker", default: "MSFT" },
        width: { definition: "chart_width", default: 800 },
      },
      scenarios: [
        { id: "microsoft", inputs: {} },
        { id: "coreweave", inputs: { symbol: "CRWV" } },
      ],
      outputs: {
        summary: {
          source: { expression: "public_summary(frame)" },
          formats: { json: {} },
        },
        chart: {
          source: "chart",
          formats: {
            image: { exporter: "png", options: { scale: 2 } },
            custom: {
              exporter: { ref: "project.exporters:network", version: "2" },
            },
          },
        },
      },
    };

    const validated = validateExportPlan(plan);

    expect(validated).toEqual(plan);
    expect(validated.scenarios?.[0]?.inputs).toEqual({});
    expect(Object.isFrozen(validated)).toBe(true);
    expect(Object.isFrozen(validated.outputs.summary)).toBe(true);
  });

  test("validates an implicit default scenario without adding it to the wire shape", () => {
    const validated = validateExportPlan({
      schema: EXPORT_PLAN_SCHEMA,
      outputs: { summary: { source: "summary", formats: { json: {} } } },
    });

    expect(validated.scenarios).toBeUndefined();
    expect(validated.inputs).toBeUndefined();
  });

  test("rejects unknown and missing scenario inputs with their input path", () => {
    const base = {
      schema: EXPORT_PLAN_SCHEMA,
      inputs: { width: { definition: "chart_width" } },
      outputs: { summary: { source: "summary", formats: { json: {} } } },
    };

    expect(() =>
      validateExportPlan({
        ...base,
        scenarios: [{ id: "bad", inputs: { unknown: 1 } }],
      }),
    ).toThrowError(
      expect.objectContaining({
        code: "invalid_plan",
        message: "plan.scenarios[0].inputs does not accept: unknown.",
        details: { path: "plan.scenarios[0].inputs" },
      }),
    );
    expect(() =>
      validateExportPlan({
        ...base,
        scenarios: [{ id: "bad", inputs: {} }],
      }),
    ).toThrowError(
      expect.objectContaining({
        code: "invalid_plan",
        message: "plan.scenarios[0].inputs is missing: width.",
        details: { path: "plan.scenarios[0].inputs" },
      }),
    );
  });

  test("rejects duplicate bindings, scenario ids, and resolved input vectors", () => {
    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        inputs: {
          first: { definition: "shared", default: 1 },
          second: { ui: "shared", default: 2 },
        },
        outputs: { summary: { source: "summary", formats: { json: {} } } },
      }),
    ).toThrow("plan.inputs bindings must be unique");

    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        scenarios: [
          { id: "same", inputs: {} },
          { id: "same", inputs: {} },
        ],
        outputs: { summary: { source: "summary", formats: { json: {} } } },
      }),
    ).toThrow("plan.scenarios ids must be unique");

    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        inputs: { width: { definition: "width", default: 800 } },
        scenarios: [
          { id: "defaulted", inputs: {} },
          { id: "explicit", inputs: { width: 800 } },
        ],
        outputs: { summary: { source: "summary", formats: { json: {} } } },
      }),
    ).toThrow("plan.scenarios must resolve to unique input vectors");
  });

  test("rejects unsafe JSON integers at the precise value path", () => {
    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        inputs: { width: { definition: "width" } },
        scenarios: [{ id: "unsafe", inputs: { width: Number.MAX_SAFE_INTEGER + 1 } }],
        outputs: { summary: { source: "summary", formats: { json: {} } } },
      }),
    ).toThrowError(
      expect.objectContaining({
        code: "invalid_plan",
        details: { path: "plan.scenarios[0].inputs.width" },
      }),
    );
  });

  test("accepts definition strings and expression objects", () => {
    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        outputs: {
          summary: {
            source: { definition: "summary" },
            formats: { json: {} },
          },
        },
      }),
    ).toThrow("must be a definition string or an expression object");

    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        outputs: {
          summary: {
            source: { cell: { name: "summary" } },
            formats: { json: {} },
          },
        },
      }),
    ).toThrow("must be a definition string or an expression object");
  });

  test("uses a format alias for an omitted exporter", () => {
    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        outputs: { summary: { source: "summary", formats: { json: {} } } },
      }),
    ).not.toThrow();

    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        outputs: { summary: { source: "summary", formats: { custom: {} } } },
      }),
    ).toThrow("must name a built-in exporter or an exporter object");
    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        outputs: {
          summary: { source: "summary", formats: { custom: { exporter: "unknown" } } },
        },
      }),
    ).toThrow("must name a built-in exporter or an exporter object");
  });

  test("rejects null format declarations", () => {
    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        outputs: { summary: { source: "summary", formats: { json: null } } },
      }),
    ).toThrow("plan.outputs.summary.formats.json must be an object");
  });

  test("validates custom exporter structure and rejects explicit null versions", () => {
    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        outputs: {
          summary: {
            source: "summary",
            formats: {
              custom: { exporter: { definition: "export_summary", version: "2" } },
            },
          },
        },
      }),
    ).not.toThrow();

    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        outputs: {
          summary: {
            source: "summary",
            formats: { custom: { exporter: { ref: "project.exporters:summary" } } },
          },
        },
      }),
    ).toThrow("must contain ref plus version, or a notebook definition");
    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        outputs: {
          summary: {
            source: "summary",
            formats: {
              custom: { exporter: { definition: "export_summary", version: null } },
            },
          },
        },
      }),
    ).toThrow("version must be a non-empty string");
    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        outputs: {
          summary: {
            source: "summary",
            formats: {
              custom: { exporter: { ref: "project:exporters:summary", version: "2" } },
            },
          },
        },
      }),
    ).toThrow("must use module:object syntax");
  });

  test("rejects unknown fields at the owning object path", () => {
    expect(() =>
      validateExportPlan({
        schema: EXPORT_PLAN_SCHEMA,
        outputs: {
          summary: {
            source: "summary",
            formats: { json: {} },
            stale: true,
          },
        },
      }),
    ).toThrowError(
      expect.objectContaining({
        code: "invalid_plan",
        message: "plan.outputs.summary does not accept: stale.",
        details: { path: "plan.outputs.summary" },
      }),
    );
  });
});
