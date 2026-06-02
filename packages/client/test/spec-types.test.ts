import type { ExportSpec, MarimoExportClient } from "@marimo-team/export-client";

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
      formats: {
        report: { export: customExport },
      },
    },
    report: {
      source: { report: { cells: [{ name: "summary" }] } },
      formats: ["markdown"],
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

const customFormatArrayItem = {
  values: {
    table: {
      source: { def: "table" },
      // @ts-expect-error Custom formats use map form so one list item cannot hide multiple names.
      formats: [{ report: { export: customExport }, summary: { export: customExport } }],
    },
  },
} satisfies ExportSpec;

const multipleBuiltinFormatArrayItem = {
  values: {
    table: {
      source: { def: "table" },
      // @ts-expect-error Format list objects contain one built-in format name.
      formats: [{ json: {}, text: {} }],
    },
  },
} satisfies ExportSpec;

const emptyReport = {
  values: {
    table: {
      // @ts-expect-error Report sources need at least one selected cell.
      source: { report: { cells: [] } },
      formats: ["markdown"],
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
void customFormatArrayItem;
void multipleBuiltinFormatArrayItem;
void emptyReport;
void unknownSourceType;

type ExportCallOptions = Parameters<MarimoExportClient["export"]>[1];
type ArchiveCallOptions = Parameters<MarimoExportClient["archive"]>[1];

const exportOptions = {
  outputRoot: "/tmp/export",
} satisfies ExportCallOptions;

const archiveOptions = {
  // @ts-expect-error Archives are in-memory results and do not write an output root.
  outputRoot: "/tmp/export",
} satisfies ArchiveCallOptions;

void exportOptions;
void archiveOptions;
