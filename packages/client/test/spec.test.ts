import assert from "node:assert/strict";
import test from "node:test";
import {
  createMarimoExportClient,
  parseExportSpec,
  safeParseExportSpec,
  type ExportSpec,
} from "@marimo-team/export-client";

const validSpecInput = {
  scenarios: [{ id: "default" }],
  values: {
    title: {
      source: { def: "title" },
      formats: ["text", { json: { filename: "title.json" } }],
    },
    report: {
      source: { report: { cells: [{ name: "summary" }] } },
      formats: ["markdown"],
    },
  },
} as const;

test("parseExportSpec accepts the public source and format shorthand", () => {
  assert.deepEqual(parseExportSpec(validSpecInput), validSpecInput);
  assert.deepEqual(
    parseExportSpec({
      values: {
        table: {
          source: { def: "table" },
          formats: {
            json: { filename: "table.json" },
            custom: {
              export: {
                type: "code",
                code: "def export(value, ctx):\n    return value\n",
              },
            },
          },
        },
      },
    }),
    {
      values: {
        table: {
          source: { def: "table" },
          formats: {
            json: { filename: "table.json" },
            custom: {
              export: {
                type: "code",
                code: "def export(value, ctx):\n    return value\n",
              },
            },
          },
        },
      },
    },
  );
  assert.deepEqual(safeParseExportSpec(validSpecInput), {
    success: true,
    spec: validSpecInput,
  });

  const result = safeParseExportSpec(validSpecInput);
  if (result.success) {
    assert.deepEqual(result.spec.values.title?.source, { def: "title" });
  } else {
    assert.fail(result.issues[0]?.message);
  }
});

test("parseExportSpec rejects invalid public spec shapes", () => {
  const invalidSpecs = [
    {
      label: "unknown built-in format",
      spec: {
        values: {
          table: {
            source: { def: "table" },
            formats: ["excel"],
          },
        },
      },
    },
    {
      label: "custom format shorthand",
      spec: {
        values: {
          table: {
            source: { def: "table" },
            formats: [{ excel: { filename: "table.xlsx" } }],
          },
        },
      },
    },
    {
      label: "custom format map shorthand",
      spec: {
        values: {
          table: {
            source: { def: "table" },
            formats: { excel: { filename: "table.xlsx" } },
          },
        },
      },
    },
    {
      label: "custom format null shorthand",
      spec: {
        values: {
          table: {
            source: { def: "table" },
            formats: { excel: null },
          },
        },
      },
    },
    {
      label: "malformed explicit built-in format config",
      spec: {
        values: {
          table: {
            source: { def: "table" },
            formats: { json: { export: "pkg:object", options: {} } },
          },
        },
      },
    },
    {
      label: "reserved built-in format options",
      spec: {
        values: {
          table: {
            source: { def: "table" },
            formats: { json: { options: {} } },
          },
        },
      },
    },
    {
      label: "explicit format null options",
      spec: {
        values: {
          table: {
            source: { def: "table" },
            formats: {
              json: {
                export: { type: "ref", ref: "pkg:object" },
                options: null,
              },
            },
          },
        },
      },
    },
    {
      label: "multi-key format list item",
      spec: {
        values: {
          table: {
            source: { def: "table" },
            formats: [{ json: {}, text: {} }],
          },
        },
      },
    },
    {
      label: "empty report source",
      spec: {
        values: {
          report: {
            source: { report: { cells: [] } },
            formats: ["markdown"],
          },
        },
      },
    },
    {
      label: "invalid code-state object",
      spec: {
        scenarios: [
          {
            id: "default",
            state: {
              width: { code: "" },
              height: { code: 1 },
            },
          },
        ],
        values: {
          table: {
            source: { def: "table" },
            formats: ["json"],
          },
        },
      },
    },
    {
      label: "undefined format map entry",
      spec: {
        values: {
          table: {
            source: { def: "table" },
            formats: { json: undefined },
          },
        },
      },
    },
    {
      label: "unknown source shorthand key",
      spec: {
        values: {
          table: {
            source: { def: "table", extra: true },
            formats: ["json"],
          },
        },
      },
    },
    {
      label: "false snapshot shorthand",
      spec: {
        values: {
          notebook: {
            source: { snapshot: false },
            formats: ["markdown"],
          },
        },
      },
    },
    {
      label: "duplicate scenario ids",
      spec: {
        scenarios: [{ id: "default" }, { id: "default" }],
        values: {
          table: {
            source: { def: "table" },
            formats: ["json"],
          },
        },
      },
    },
    {
      label: "code-state expression object",
      spec: {
        scenarios: [
          {
            id: "default",
            state: {
              width: { type: "code", expression: "800" },
            },
          },
        ],
        values: {
          table: {
            source: { def: "table" },
            formats: ["json"],
          },
        },
      },
    },
    {
      label: "code-state marker object",
      spec: {
        scenarios: [
          {
            id: "default",
            state: {
              width: { type: "code" },
            },
          },
        ],
        values: {
          table: {
            source: { def: "table" },
            formats: ["json"],
          },
        },
      },
    },
  ];

  for (const { label, spec } of invalidSpecs) {
    assert.throws(() => parseExportSpec(spec), Error, label);
    assert.equal(safeParseExportSpec(spec).success, false, label);
  }
});

test("MarimoExportClient validates specs before contacting marimo", async () => {
  const requests: Request[] = [];
  const client = createMarimoExportClient({
    server: "https://marimo.example.test",
    fetch: async (request) => {
      requests.push(request);
      return new Response("unexpected", { status: 500 });
    },
  });

  await assert.rejects(
    client.export(
      {
        values: {
          table: {
            source: { type: "query", sql: "select 1" },
            formats: ["json"],
          },
        },
      } as unknown as ExportSpec,
      { sessionId: "session-1" },
    ),
    /Invalid type/,
  );

  assert.equal(requests.length, 0);
});
