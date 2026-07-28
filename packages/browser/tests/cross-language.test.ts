import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vite-plus/test";

import { openPublication, scalarLoader } from "../src/index.js";
import { canonicalJson } from "../src/schema.js";
import canonicalCases from "../../../tests/fixtures/canonical-json/cases.json" with { type: "json" };

const scalarIndex = fileURLToPath(
  new URL("../../../tests/fixtures/publication/scalar-index.json", import.meta.url),
);

describe("Python and TypeScript protocol fixtures", () => {
  test.each(canonicalCases)("$name canonicalizes to the Python bytes", ({ value, canonical }) => {
    expect(canonicalJson(value)).toBe(canonical);
  });

  test("opens a canonical PublicationIndex emitted by Python", async () => {
    const index = (await readFile(scalarIndex, "utf8")).trimEnd();
    const publication = await openPublication("https://example.test/fixture", {
      fetch: async () => new Response(index),
    });

    expect(publication.states().map((state) => state.name)).toEqual(["one", "two"]);
    await expect(publication.state("one").output("answer").load(scalarLoader())).resolves.toBe(42);
    await expect(publication.state("two").output("answer").load(scalarLoader())).resolves.toBe(
      9007199254740992n,
    );
  });
});
