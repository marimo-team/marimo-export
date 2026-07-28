import { describe, expect, test } from "vite-plus/test";

import * as api from "../src/index.js";

describe("browser public API", () => {
  test("exposes one publication entrypoint and one error class", () => {
    expect(Object.keys(api).sort()).toEqual(["PublicationError", "openPublication"]);
  });
});
