import { describe, expect, test } from "vite-plus/test";

import { openExport } from "../src/index.js";
import type { MutableJsonObject } from "./fixture.js";
import { exportFixture, mutableObject } from "./fixture.js";
import filenameFixture from "../../../tests/fixtures/export/portable-filenames.json" with { type: "json" };

type FilenameSurface = "blob" | "notebook";

describe("portable filename policy", () => {
  for (const surface of filenameFixture.surfaces) {
    if (!isFilenameSurface(surface)) throw new TypeError(`Unknown filename surface ${surface}.`);
    test.each(filenameFixture.cases)(`${surface}: $name`, async ({ value, valid }) => {
      const indexTransform = (index: MutableJsonObject) => {
        if (surface === "notebook") {
          mutableObject(index.notebook, "notebook").filename = value;
        }
      };
      const fixture = await exportFixture(
        surface === "blob" ? { blobFilename: value, indexTransform } : { indexTransform },
      );
      const opened = openExport("https://example.test/stocks", { fetch: fixture.fetch });

      if (!valid) {
        await expect(opened).rejects.toMatchObject({ code: "export_invalid" });
        return;
      }
      const exported = await opened;
      if (surface === "notebook") {
        expect(exported.notebook.filename).toBe(value);
      } else {
        expect(exported.state("alpha").output("view").descriptor).toMatchObject({
          filename: value,
        });
      }
    });
  }
});

function isFilenameSurface(value: string): value is FilenameSurface {
  return value === "blob" || value === "notebook";
}
