import { describe, expect, test } from "vite-plus/test";

import { moduleUrl } from "./fixture.js";
import { anyWidgetModuleCacheKey } from "../src/runtime/module-cache.js";

describe("AnyWidget module cache identity", () => {
  test("uses the canonical embedded key and content with the declared hash", () => {
    const source = moduleUrl("export default {};");
    const files = { "/@file/widget.js": source };
    const canonical = anyWidgetModuleCacheKey(
      { hash: "verified-hash", url: "/@file/widget.js" },
      files,
    );
    const alias = anyWidgetModuleCacheKey(
      { hash: "verified-hash", url: "./@file/widget.js" },
      files,
    );
    const changedSource = anyWidgetModuleCacheKey(
      { hash: "verified-hash", url: "/@file/widget.js" },
      { "/@file/widget.js": moduleUrl("export default { render() {} };") },
    );
    const changedHash = anyWidgetModuleCacheKey(
      { hash: "another-hash", url: "/@file/widget.js" },
      files,
    );

    expect(alias).toBe(canonical);
    expect(changedSource).not.toBe(canonical);
    expect(changedHash).not.toBe(canonical);
  });

  test("keys a data URL module by its exact source", () => {
    const source = moduleUrl("export default {};");
    const other = moduleUrl("export default { render() {} };");
    const spec = { hash: "verified-hash", url: source };

    expect(anyWidgetModuleCacheKey(spec, {})).toBe(anyWidgetModuleCacheKey({ ...spec }, {}));
    expect(anyWidgetModuleCacheKey({ ...spec, url: other }, {})).not.toBe(
      anyWidgetModuleCacheKey(spec, {}),
    );
  });
});
