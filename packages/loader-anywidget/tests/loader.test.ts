import { describe, expect, expectTypeOf, test } from "vite-plus/test";
import producerPayload from "../../python/tests/fixtures/anywidget-v1.json";
import httpModuleUrlCases from "../../../tests/fixtures/export/http-module-urls.json" with { type: "json" };
import { loadAnyWidget, type LoadedAnyWidget } from "../src/index.js";
import { loadPayload, moduleUrl, notification, payload } from "./fixture.js";

interface HttpModuleUrlCase {
  readonly name: string;
  readonly url: string;
  readonly valid: boolean;
}

describe("anywidget", () => {
  test("accepts state interfaces with required and optional fields", () => {
    interface MapState {
      zoom: number;
      label?: string;
    }

    expectTypeOf(loadAnyWidget<MapState>).returns.toEqualTypeOf<
      Promise<LoadedAnyWidget<MapState>>
    >();
    expectTypeOf<LoadedAnyWidget<MapState>["initialState"]["label"]>().toEqualTypeOf<
      string | undefined
    >();
  });

  test("loads the producer contract fixture", async () => {
    const loaded = await loadPayload<{ child: string; binary: { view: DataView } }>(
      producerPayload,
    );

    expect(loaded.initialState.child).toBe("anywidget:model-1");
    expect([...new Uint8Array(loaded.initialState.binary.view.buffer)]).toEqual([1, 2, 3]);
  });

  test.each(httpModuleUrlCases as readonly HttpModuleUrlCase[])(
    "$name at the AnyWidget loader boundary",
    async ({ url, valid }) => {
      const loading = loadPayload(
        payload({
          modelNotifications: [notification({ id: "model-0", state: {}, moduleUrl: url })],
        }),
      );
      if (valid) {
        await expect(loading).resolves.toBeDefined();
      } else {
        await expect(loading).rejects.toThrow();
      }
    },
  );

  test("loads state and buffers without executing the frontend module", async () => {
    const marker = "__marimoExportAnyWidgetLoaded";
    Reflect.deleteProperty(globalThis, marker);
    const url = moduleUrl(`globalThis.${marker} = true; export default { render() {} };`);
    const loaded = await loadPayload<{ count: number; binary: { view: DataView } }>(
      payload({
        modelNotifications: [
          notification({
            id: "model-0",
            state: { count: 2, binary: {} },
            moduleUrl: url,
            bufferPaths: [["binary", "view"]],
            buffers: ["AQID"],
          }),
        ],
      }),
    );

    expect(Reflect.has(globalThis, marker)).toBe(false);
    expect(loaded.initialState.count).toBe(2);
    expect([...new Uint8Array(loaded.initialState.binary.view.buffer)]).toEqual([1, 2, 3]);
    expect(Object.hasOwn(loaded.initialState.binary, "view")).toBe(true);
    expect(Object.isFrozen(loaded.initialState)).toBe(true);
    await expect(loaded.mount({} as HTMLElement)).rejects.toThrow(
      "AnyWidget mount requires a browser element",
    );
  });

  test("rejects unresolved model references before module execution", async () => {
    const marker = "__marimoExportDanglingModule";
    Reflect.deleteProperty(globalThis, marker);
    const loading = loadPayload(
      payload({
        modelNotifications: [
          notification({
            id: "model-0",
            state: { child: "anywidget:missing" },
            moduleUrl: moduleUrl(`globalThis.${marker} = true; export default { render() {} };`),
          }),
        ],
      }),
    );

    await expect(loading).rejects.toThrow('reference "missing" is unresolved');
    expect(Reflect.has(globalThis, marker)).toBe(false);
  });

  test("requires each buffer path parent to exist", async () => {
    const loading = loadPayload(
      payload({
        modelNotifications: [
          notification({
            id: "model-0",
            state: {},
            moduleUrl: moduleUrl("export default { render() {} }"),
            bufferPaths: [["binary", "view"]],
            buffers: ["AQID"],
          }),
        ],
      }),
    );

    await expect(loading).rejects.toThrow("does not target existing state");
  });

  test("rejects reserved buffer tokens without changing the source prototype", async () => {
    const binary = {};
    const prototype = Object.getPrototypeOf(binary);
    const loading = loadPayload(
      payload({
        modelNotifications: [
          notification({
            id: "model-0",
            state: { binary },
            moduleUrl: moduleUrl("export default { render() {} }"),
            bufferPaths: [["binary", "__proto__"]],
            buffers: ["AQID"],
          }),
        ],
      }),
    );

    await expect(loading).rejects.toThrow("invalid buffer path token");
    expect(Object.getPrototypeOf(binary)).toBe(prototype);
    expect(Object.hasOwn(binary, "__proto__")).toBe(false);
  });

  test("rejects virtual ESM files that are absent from the payload", async () => {
    const loading = loadPayload(
      payload({
        modelNotifications: [
          notification({ id: "model-0", state: {}, moduleUrl: "/@file/widget.js" }),
        ],
      }),
    );

    await expect(loading).rejects.toThrow("missing virtual file");
  });

  test("accepts Marimo's normalized file key for a relative ESM URL", async () => {
    const loading = loadPayload(
      payload({
        files: {
          "/@file/widget.js": moduleUrl("export default { render() {} }"),
        },
        modelNotifications: [
          notification({ id: "model-0", state: {}, moduleUrl: "./@file/widget.js" }),
        ],
      }),
    );

    await expect(loading).resolves.toBeDefined();
  });

  test("rejects malformed embedded modules while loading", async () => {
    const loading = loadPayload(
      payload({
        files: { "/@file/widget.js": "data:text/javascript;base64,%%%" },
        modelNotifications: [
          notification({ id: "model-0", state: {}, moduleUrl: "/@file/widget.js" }),
        ],
      }),
    );

    await expect(loading).rejects.toThrow("malformed base64 data");
  });

  test("requires the canonical root model ID", async () => {
    const loading = loadPayload(
      payload({
        rootModelId: "root",
        modelNotifications: [
          notification({
            id: "model-0",
            state: {},
            moduleUrl: moduleUrl("export default { render() {} }"),
          }),
        ],
      }),
    );

    await expect(loading).rejects.toThrow('rootModelId must be "model-0"');
  });
});
