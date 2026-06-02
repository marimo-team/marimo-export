import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

import { parseExportSpec, type ExportSpec } from "@marimo-team/export-client";

interface ElementStub {
  value: string;
  textContent: string;
  innerHTML: string;
  href: string;
  addEventListener(): void;
  classList: {
    toggle(): void;
  };
}

test("frameworkless server archive page builds a valid public export spec", () => {
  const htmlPath = path.resolve(process.cwd(), "../../examples/frameworkless/server-archive.html");
  const html = readFileSync(htmlPath, "utf-8");
  const script = html.match(/<script type="module">([\s\S]*?)<\/script>/)?.[1];
  assert.ok(script);

  const elements = new Map<string, ElementStub>();
  const context = {
    parseExportSpec,
    createMarimoExportClient: () => null,
    createMarimoWorkspaceClient: () => null,
    readExport: async () => null,
    document: {
      querySelector(selector: string): ElementStub {
        let element = elements.get(selector);
        if (!element) {
          element = {
            value: "",
            textContent: "",
            innerHTML: "",
            href: "",
            addEventListener() {},
            classList: {
              toggle() {},
            },
          };
          elements.set(selector, element);
        }
        return element;
      },
    },
  };

  const executable = `${withoutImports(script)}
globalThis.__queueingSpec = createQueueingArchiveSpec();
globalThis.__renderedSpec = document.querySelector("#spec").value;`;

  vm.runInNewContext(executable, context, { filename: htmlPath });

  const spec = parseExportSpec(
    (context as typeof context & { __queueingSpec: ExportSpec }).__queueingSpec,
  );
  const renderedSpec = JSON.parse(
    (context as typeof context & { __renderedSpec: string }).__renderedSpec,
  ) as unknown;

  assert.deepEqual(
    JSON.parse(JSON.stringify(parseExportSpec(renderedSpec))),
    JSON.parse(JSON.stringify(spec)),
  );
  assert.deepEqual(spec.scenarios?.[0]?.state?.arrival_rate, { code: "6.0" });
  assert.deepEqual(spec.values.summary?.source, { def: "queue_summary" });
});

function withoutImports(script: string): string {
  return script
    .replace(/^\s*import\s+\{[\s\S]*?\}\s+from\s+"[^"]+";\s*/gm, "")
    .replace(/^\s*import\s+[^;]+;\s*/gm, "");
}
