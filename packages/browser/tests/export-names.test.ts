import { describe, expect, test } from "vite-plus/test";

import { openExport } from "../src/index.js";
import type { MutableJsonObject } from "./fixture.js";
import { exportFixture, mutableArray, mutableObject, stringValue } from "./fixture.js";
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
  index: MutableJsonObject,
  surface: ExportNameSurface,
  value: string,
): void {
  if (surface === "alias") {
    const aliases = mutableObject(index.aliases, "aliases");
    aliases[value] = stringValue(aliases.alpha, "aliases.alpha");
    delete aliases.alpha;
    return;
  }
  index.outputs = mutableArray(index.outputs, "outputs").map((name) =>
    name === "count" ? value : name,
  );
  for (const state of Object.values(mutableObject(index.states, "states"))) {
    const outputs = mutableObject(mutableObject(state, "state").outputs, "state.outputs");
    const count = outputs.count;
    if (count === undefined) throw new TypeError("State fixture count output is missing.");
    outputs[value] = count;
    delete outputs.count;
  }
}
