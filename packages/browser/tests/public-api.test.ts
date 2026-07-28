import { expect, test } from "vite-plus/test";

import * as api from "../src/index.js";

test("exports the browser core contract", () => {
  expect(Object.keys(api).sort()).toEqual([
    "PublicationError",
    "defineBlobAssetLoader",
    "defineOutputLoader",
    "imageLoader",
    "openPublication",
    "resolveOutputLoader",
    "scalarLoader",
  ]);
});
