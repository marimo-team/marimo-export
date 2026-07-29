import type { BlobAssetLoader } from "@marimo-team/marimo-export";
import { describe, expect, expectTypeOf, test } from "vite-plus/test";
import producerPayload from "../../python/tests/fixtures/anywidget-v1.json";
import { anyWidgetLoader, type LoadedAnyWidget } from "../src/index.js";
import { moduleUrl, notification, outputFor, payload } from "./fixture.js";

describe("anywidget", () => {
  test("accepts state interfaces with required and optional fields", () => {
    interface MapState {
      zoom: number;
      label?: string;
    }

    const loader = anyWidgetLoader<MapState>();

    expect(loader.codec).toBe("marimo.blob-asset.msgpack.v1");
    expectTypeOf(loader).toEqualTypeOf<BlobAssetLoader<LoadedAnyWidget<MapState>>>();
    expectTypeOf<LoadedAnyWidget<MapState>["initialState"]["label"]>().toEqualTypeOf<
      string | undefined
    >();
  });

  test("loads the producer contract fixture", async () => {
    const output = await outputFor(producerPayload);

    const loaded =
      await output.load(anyWidgetLoader<{ child: string; binary: { view: DataView } }>());

    expect(loaded.initialState.child).toBe("anywidget:model-1");
    expect([...new Uint8Array(loaded.initialState.binary.view.buffer)]).toEqual([1, 2, 3]);
  });

  test("loads state and buffers without executing the frontend module", async () => {
    const marker = "__marimoExportAnyWidgetLoaded";
    Reflect.deleteProperty(globalThis, marker);
    const url = moduleUrl(`globalThis.${marker} = true; export default { render() {} };`);
    const output = await outputFor(
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

    const loaded =
      await output.load(anyWidgetLoader<{ count: number; binary: { view: DataView } }>());

    expect(Reflect.has(globalThis, marker)).toBe(false);
    expect(loaded.initialState.count).toBe(2);
    expect([...new Uint8Array(loaded.initialState.binary.view.buffer)]).toEqual([1, 2, 3]);
    expect(Object.isFrozen(loaded.initialState)).toBe(true);
    await expect(loaded.mount({} as HTMLElement)).rejects.toThrow(
      "AnyWidget mount requires a browser element",
    );
  });

  test("validates the media type through the loader boundary", async () => {
    const output = await outputFor(
      payload({
        modelNotifications: [
          notification({ id: "model-0", state: {}, moduleUrl: moduleUrl("export default {}") }),
        ],
      }),
      { mediaType: "application/json" },
    );

    await expect(output.load(anyWidgetLoader())).rejects.toThrow("No OutputLoader accepts");
  });

  test("rejects unresolved model references before module execution", async () => {
    const marker = "__marimoExportDanglingModule";
    Reflect.deleteProperty(globalThis, marker);
    const output = await outputFor(
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

    await expect(output.load(anyWidgetLoader())).rejects.toThrow(
      'reference "missing" is unresolved',
    );
    expect(Reflect.has(globalThis, marker)).toBe(false);
  });

  test("requires each buffer path parent to exist", async () => {
    const output = await outputFor(
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

    await expect(output.load(anyWidgetLoader())).rejects.toThrow("does not target existing state");
  });

  test("rejects virtual ESM files that are absent from the payload", async () => {
    const output = await outputFor(
      payload({
        modelNotifications: [
          notification({ id: "model-0", state: {}, moduleUrl: "/@file/widget.js" }),
        ],
      }),
    );

    await expect(output.load(anyWidgetLoader())).rejects.toThrow("missing virtual file");
  });

  test("rejects malformed embedded modules while loading", async () => {
    const output = await outputFor(
      payload({
        files: { "/@file/widget.js": "data:text/javascript;base64,%%%" },
        modelNotifications: [
          notification({ id: "model-0", state: {}, moduleUrl: "/@file/widget.js" }),
        ],
      }),
    );

    await expect(output.load(anyWidgetLoader())).rejects.toThrow("malformed base64 data");
  });

  test("treats a base64 media type as percent-encoded data", async () => {
    const output = await outputFor(
      payload({
        files: { "/@file/widget.js": "data:base64,export%20default%20%7B%7D" },
        modelNotifications: [
          notification({ id: "model-0", state: {}, moduleUrl: "/@file/widget.js" }),
        ],
      }),
    );

    await expect(output.load(anyWidgetLoader())).resolves.toBeDefined();
  });

  test("requires the canonical root model ID", async () => {
    const output = await outputFor(
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

    await expect(output.load(anyWidgetLoader())).rejects.toThrow('rootModelId must be "model-0"');
  });

  test("requires model notifications in canonical order", async () => {
    const output = await outputFor(
      payload({
        modelNotifications: [
          notification({
            id: "model-1",
            state: {},
            moduleUrl: moduleUrl("export default { render() {} }"),
          }),
        ],
      }),
    );

    await expect(output.load(anyWidgetLoader())).rejects.toThrow(
      'modelNotifications[0].model_id must be "model-0"',
    );
  });

  test("bounds and escapes unexpected payload fields", async () => {
    const fields = Object.fromEntries(
      Array.from({ length: 20 }, (_, index) => [
        `${index === 0 ? "\u009b" : "field"}-${index}-${"x".repeat(4_000)}`,
        true,
      ]),
    );
    const output = await outputFor({ ...payload({ modelNotifications: [] }), ...fields });

    let message = "";
    try {
      await output.load(anyWidgetLoader());
    } catch (error) {
      if (error instanceof Error) message = error.message;
    }

    expect(message).toContain("Unexpected:");
    expect(message).toContain("\\u009b");
    expect(message).not.toContain("\u009b");
    expect(message).toContain("(+12 more)");
    expect(message.length).toBeLessThanOrEqual(2_048);
  });

  test("bounds a file-path diagnostic", async () => {
    const path = `\u009b${"file".repeat(300_000)}`;
    const output = await outputFor({
      ...payload({ modelNotifications: [] }),
      files: { [path]: "not-a-data-url" },
    });

    let message = "";
    try {
      await output.load(anyWidgetLoader());
    } catch (error) {
      if (error instanceof Error) message = error.message;
    }

    expect(message.length).toBeLessThan(256);
    expect(message).toContain("\\u009b");
    expect(message).not.toContain("\u009b");
    expect(message).toContain("...");
  });

  test("bounds the unrelated-model list", async () => {
    const notifications = [
      notification({
        id: "model-0",
        state: {},
        moduleUrl: moduleUrl("export default { render() {} }"),
      }),
      ...Array.from({ length: 20 }, (_, index) =>
        notification({ id: `model-${index + 1}`, state: {} }),
      ),
    ];
    const output = await outputFor(payload({ modelNotifications: notifications }));

    await expect(output.load(anyWidgetLoader())).rejects.toThrow("(+12 more)");
  });
});
