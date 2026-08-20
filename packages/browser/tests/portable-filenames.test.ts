import { describe, expect, test } from "vite-plus/test";

import { openExport } from "../src/index.js";
import { exportFixture } from "./fixture.js";
import filenameFixture from "../../../tests/fixtures/export/portable-filenames.json" with { type: "json" };

type FilenameSurface = "blob" | "notebook";

describe("portable filename policy", () => {
  for (const surface of filenameFixture.surfaces as readonly FilenameSurface[]) {
    test.each(filenameFixture.cases)(`${surface}: $name`, async ({ value, valid }) => {
      const fixture = await exportFixture({
        ...(surface === "blob" ? { blobFilename: value } : {}),
        indexTransform: (index) => {
          if (surface === "notebook") {
            (index.notebook as Record<string, unknown>).filename = value;
          }
        },
      });
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
