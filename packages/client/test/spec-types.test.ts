import {
  parseExportSpec,
  type ExportSpecInput,
  type MarimoExportClient,
  type Runtime,
  type RuntimeOption,
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
      formats: [{ format: "report", export: customExport }],
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
} satisfies ExportSpecInput;

const rawExportSpec: ExportSpecArgument = rawSpec;

const generatedSpec = {
  values: {
    table: {
      source: { def: "table" },
      formats: [
        {
          format: "excel",
          export: customExport,
          options: { filename: "table.xlsx" },
        },
      ],
    },
  },
} satisfies ExportSpecInput;

const invalidSourceSpec = {
  values: {
    table: {
      // @ts-expect-error Query sources are not part of the export spec contract.
      source: { query: "select 1" },
      formats: ["json"],
    },
  },
} satisfies ExportSpecInput;

const emptyFormatListSpec = {
  values: {
    table: {
      source: { def: "table" },
      // @ts-expect-error Format lists need at least one item.
      formats: [],
    },
  },
} satisfies ExportSpecInput;

const unknownFormatListSpec = {
  values: {
    table: {
      source: { def: "table" },
      // @ts-expect-error Unknown format names need an explicit list entry with export.
      formats: ["excel"],
    },
  },
} satisfies ExportSpecInput;

const customFormatMapShorthandSpec = {
  values: {
    table: {
      source: { def: "table" },
      formats: {
        // @ts-expect-error Custom format maps need an explicit list entry with format and export.
        excel: { filename: "table.xlsx" },
      },
    },
  },
} satisfies ExportSpecInput;

const customFormatNullMapSpec = {
  values: {
    table: {
      source: { def: "table" },
      formats: {
        // @ts-expect-error Custom format maps need an explicit list entry with format and export.
        excel: null,
      },
    },
  },
} satisfies ExportSpecInput;

const customExplicitMapSpec = {
  values: {
    table: {
      source: { def: "table" },
      formats: {
        // @ts-expect-error Custom formats use explicit list entries.
        excel: { export: customExport },
      },
    },
  },
} satisfies ExportSpecInput;

const malformedBuiltinExplicitSpec = {
  values: {
    table: {
      source: { def: "table" },
      formats: {
        // @ts-expect-error Built-in format options reserve the export key for explicit configs.
        json: { export: "pkg:object" },
      },
    },
  },
} satisfies ExportSpecInput;

void validSpec;
void exportSpecArgument;
void rawExportSpec;
void generatedSpec;
void invalidSourceSpec;
void emptyFormatListSpec;
void unknownFormatListSpec;
void customFormatMapShorthandSpec;
void customFormatNullMapSpec;
void customExplicitMapSpec;
void malformedBuiltinExplicitSpec;

type ExportCallOptions = Parameters<MarimoExportClient["export"]>[1];
type ArchiveCallOptions = Parameters<MarimoExportClient["archive"]>[1];

const exportOptions = {
  outputRoot: "/tmp/export",
} satisfies ExportCallOptions;

const archiveOptions = {
  // @ts-expect-error Archives are in-memory results and do not write an output root.
  outputRoot: "/tmp/export",
} satisfies ArchiveCallOptions;

const runtimeInstall = {
  package: "moexport[all]",
  manager: "uv",
  source: "kernel",
} satisfies Runtime;

const runtimeOption = runtimeInstall satisfies RuntimeOption;

const invalidRuntimeInstall = {
  module: "moexport",
  // @ts-expect-error Runtime install requests need a package specifier.
} satisfies Runtime;

void exportOptions;
void archiveOptions;
void runtimeInstall;
void runtimeOption;
void invalidRuntimeInstall;
