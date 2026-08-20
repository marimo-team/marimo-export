import { describe, expect, test } from "vite-plus/test";

import { moduleUrl } from "./fixture.js";
import { anyWidgetModuleCacheKey } from "../src/runtime/module-cache.js";

describe("AnyWidget module cache identity", () => {
  test("stores a bounded digest instead of embedded module source", async () => {
    const source = `export default {}; // ${"base64-source".repeat(10_000)}`;
    const embedded = moduleUrl(source);
    const key = await anyWidgetModuleCacheKey(
      { hash: "verified-hash", url: "/@file/widget.js" },
      { "/@file/widget.js": embedded },
    );
    const dataUrlKey = await anyWidgetModuleCacheKey({ hash: "verified-hash", url: embedded }, {});

    expect(key).toMatch(/^[0-9a-f]{64}$/u);
    expect(key).not.toContain("base64-source");
    expect(key.length).toBe(64);
    expect(dataUrlKey).toMatch(/^[0-9a-f]{64}$/u);
    expect(dataUrlKey).not.toContain("base64-source");
  });

  test("uses the canonical embedded key and content with the declared hash", async () => {
    const source = moduleUrl("export default {};");
    const files = { "/@file/widget.js": source };
    const canonical = await anyWidgetModuleCacheKey(
      { hash: "verified-hash", url: "/@file/widget.js" },
      files,
    );
    const alias = await anyWidgetModuleCacheKey(
      { hash: "verified-hash", url: "./@file/widget.js" },
      files,
    );
    const changedSource = await anyWidgetModuleCacheKey(
      { hash: "verified-hash", url: "/@file/widget.js" },
      { "/@file/widget.js": moduleUrl("export default { render() {} };") },
    );
    const changedHash = await anyWidgetModuleCacheKey(
      { hash: "another-hash", url: "/@file/widget.js" },
      files,
    );

    expect(alias).toBe(canonical);
    expect(changedSource).not.toBe(canonical);
    expect(changedHash).not.toBe(canonical);
  });
});
