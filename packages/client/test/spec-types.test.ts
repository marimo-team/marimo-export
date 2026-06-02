import type { ExportSpec } from "@marimo-team/export-client";

const customExport = {
  type: "code",
  code: "def export(value, ctx):\n    return value\n",
} as const;

const validSpec = {
  values: {
    title: {
      source: { def: "title" },
      formats: ["text", { json: { filename: "title.json" } }],
    },
    snapshot: {
      source: { snapshot: true, include_source: false },
      formats: [{ report: { export: customExport } }],
    },
  },
} satisfies ExportSpec;

const unknownFormatName = {
  values: {
    table: {
      source: { def: "table" },
      // @ts-expect-error Unknown built-in shorthand needs an explicit exporter record.
      formats: ["excel"],
    },
  },
} satisfies ExportSpec;

const customFormatOptions = {
  values: {
    table: {
      source: { def: "table" },
      // @ts-expect-error Custom formats cannot use built-in option shorthand.
      formats: [{ excel: { filename: "table.xlsx" } }],
    },
  },
} satisfies ExportSpec;

const unknownSourceType = {
  values: {
    table: {
      // @ts-expect-error Source type names mirror the Python spec discriminators.
      source: { type: "query", sql: "select 1" },
      formats: ["json"],
    },
  },
} satisfies ExportSpec;

void validSpec;
void unknownFormatName;
void customFormatOptions;
void unknownSourceType;
