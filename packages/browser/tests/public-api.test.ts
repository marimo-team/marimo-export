import { expect, test } from "vite-plus/test";

import * as api from "../src/index.js";
import * as anyWidgetApi from "../src/loader/anywidget.js";
import * as htmlApi from "../src/loader/html.js";
import * as jsonApi from "../src/loader/json.js";
import * as marimoCellApi from "../src/loader/marimo-cell.js";
import * as marimoOutputApi from "../src/loader/marimo-output.js";
import * as preparedApi from "../src/prepared/index.js";
import * as textApi from "../src/loader/text.js";

test("exports the browser core contract", () => {
  expect(Object.keys(api).sort()).toEqual([
    "NotebookExportError",
    "defineBlobAssetLoader",
    "defineOutputLoader",
    "imageLoader",
    "isNotebookExportError",
    "openExport",
    "parseMarimoCellSnapshot",
    "parseMarimoOutputSnapshot",
    "resolveOutputLoader",
    "scalarLoader",
  ]);
});

test("exports the projection loader subpaths", () => {
  expect(Object.keys(jsonApi)).toEqual(["jsonLoader"]);
  expect(Object.keys(marimoCellApi)).toEqual(["marimoCellLoader"]);
  expect(Object.keys(marimoOutputApi)).toEqual(["marimoOutputLoader"]);
});

test("exports the AnyWidget loader and prepared graph", () => {
  expect(Object.keys(anyWidgetApi).sort()).toEqual([
    "PreparedWidgetGraph",
    "PreparedWidgetGraphReplacementError",
    "anyWidgetLoader",
  ]);
});

test("exports the UTF-8 loader subpaths", () => {
  expect(Object.keys(textApi)).toEqual(["textLoader"]);
  expect(Object.keys(htmlApi)).toEqual(["htmlLoader"]);
});

test("exports the prepared-state application subpath", () => {
  expect(Object.keys(preparedApi).sort()).toEqual([
    "PreparedExportError",
    "PreparedPublicationRefresh",
    "PreparedStateController",
    "fetchPreparedExportManifest",
    "isPreparedAbort",
    "isPreparedExportError",
    "openPreparedPublication",
    "parsePreparedExportManifest",
    "preparedControlInputPatch",
    "resolvePreparedPublication",
    "resolvePreparedQuerySelection",
    "resolvePreparedQueryState",
    "samePreparedInputs",
  ]);
});
