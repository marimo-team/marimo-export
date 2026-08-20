import { describe, expect, test } from "vite-plus/test";

import { openExport } from "../src/index.js";
import { exportFixture } from "./fixture.js";
import exportNameCases from "../../../tests/fixtures/export/export-names.json" with { type: "json" };

type ExportNameSurface = "alias" | "output";

describe("export name policy", () => {
  for (const surface of ["alias", "output"] as const satisfies readonly ExportNameSurface[]) {
    test.each(exportNameCases)(`${surface}: $name`, async ({ value, valid }) => {
      const fixture = await exportFixture({
        indexTransform: (index) => renameExportName(index, surface, value),
      });
      const opened = openExport("https://example.test/stocks", { fetch: fixture.fetch });

      if (!valid) {
        await expect(opened).rejects.toMatchObject({ code: "export_invalid" });
        return;
      }
      const exported = await opened;
      if (surface === "alias") {
        expect(exported.state(value).aliases).toContain(value);
      } else {
        expect(exported.outputNames).toContain(value);
      }
    });
  }
});

function renameExportName(
  index: Record<string, unknown>,
  surface: ExportNameSurface,
  value: string,
): void {
  if (surface === "alias") {
    const aliases = index.aliases as Record<string, string>;
    aliases[value] = aliases.alpha!;
    delete aliases.alpha;
    return;
  }
  index.outputs = (index.outputs as string[]).map((name) => (name === "count" ? value : name));
  for (const state of Object.values(index.states as Record<string, unknown>)) {
    const outputs = (state as { outputs: Record<string, unknown> }).outputs;
    outputs[value] = outputs.count;
    delete outputs.count;
  }
}
