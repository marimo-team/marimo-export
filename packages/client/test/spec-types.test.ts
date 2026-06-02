import {
  parseExportSpec,
  type FormatMap,
  type MarimoExportClient,
} from "@marimo-team/export-client";

const customExport = {
  type: "code",
  code: "def export(value, ctx):\n    return value\n",
} as const;

const validSpec = parseExportSpec({
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
});

type ExportSpecArgument = Parameters<MarimoExportClient["export"]>[0];

const exportSpecArgument: ExportSpecArgument = validSpec;

const rawSpec = {
  values: {
    table: {
      source: { def: "table" },
      formats: ["json"],
    },
  },
};

// @ts-expect-error Export calls require a spec returned by parseExportSpec.
const rawExportSpec: ExportSpecArgument = rawSpec;

// @ts-expect-error FormatMap is parser output, so custom keys need runtime validation.
const customFormatOptions: FormatMap = { excel: { filename: "table.xlsx" } };

// @ts-expect-error Custom format maps cannot use null shorthand.
const customFormatNull: FormatMap = { excel: null };

void validSpec;
void exportSpecArgument;
void rawExportSpec;
void customFormatOptions;
void customFormatNull;

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
