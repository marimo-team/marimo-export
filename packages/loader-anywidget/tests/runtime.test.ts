import { anyWidgetLoader } from "@marimo-team/marimo-export-loader-anywidget";
import { afterEach, beforeEach, describe, expect, test, vi } from "vite-plus/test";
import { combineAbortSignals } from "../src/runtime/abort.js";
import { resolveAnyWidgetModule } from "../src/runtime/binding.js";
import { modelProxy } from "../src/runtime/model-proxy.js";
import { base64ModuleUrl, moduleUrl, notification, outputFor, payload } from "./fixture.js";

interface Counters {
  rootInitialize: number;
  rootRender: number;
  rootRenderCleanup: number;
  rootAbort: number;
  childInitialize: number;
  childInitializeCleanup: number;
  childRender: number;
  childRenderCleanup: number;
  sendCallback: number;
}

let documentValue: FakeDocument;

beforeEach(() => {
  documentValue = new FakeDocument();
  vi.stubGlobal("document", documentValue);
  vi.stubGlobal("window", {});
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("AnyWidget browser runtime", () => {
  test("bounds an invalid module URL diagnostic", () => {
    const url = `data:text/javascript,${"x".repeat(1_000_000)}`;

    expect(() => resolveAnyWidgetModule({}, url)).toThrowError(
      expect.objectContaining({
        message: expect.stringMatching(/^AnyWidget module ".*\.\.\." must default-export/),
      }),
    );
    try {
      resolveAnyWidgetModule({}, url);
    } catch (error) {
      expect((error as Error).message.length).toBeLessThan(256);
      expect((error as Error).message).not.toContain("x".repeat(128));
    }
  });

  test("removes fallback listeners after the first combined signal aborts", () => {
    const descriptor = Object.getOwnPropertyDescriptor(AbortSignal, "any");
    Object.defineProperty(AbortSignal, "any", { configurable: true, value: undefined });
    try {
      const first = new AbortController();
      const second = new AbortController();
      const firstAdd = vi.spyOn(first.signal, "addEventListener");
      const firstRemove = vi.spyOn(first.signal, "removeEventListener");
      const secondRemove = vi.spyOn(second.signal, "removeEventListener");

      const combined = combineAbortSignals([first.signal, first.signal, second.signal]);
      first.abort("first");

      expect(combined.aborted).toBe(true);
      expect(combined.reason).toBe("first");
      expect(firstAdd).toHaveBeenCalledOnce();
      expect(firstRemove).toHaveBeenCalledOnce();
      expect(secondRemove).toHaveBeenCalledOnce();
    } finally {
      if (descriptor === undefined) Reflect.deleteProperty(AbortSignal, "any");
      else Object.defineProperty(AbortSignal, "any", descriptor);
    }
  });

  test("removes model abort listeners when callbacks are unregistered", () => {
    const controller = new AbortController();
    const add = vi.spyOn(controller.signal, "addEventListener");
    const remove = vi.spyOn(controller.signal, "removeEventListener");
    const on = vi.fn();
    const off = vi.fn();
    const model = {
      get: vi.fn(),
      set: vi.fn(),
      save_changes: vi.fn(),
      send: vi.fn(),
      on,
      off,
      widget_manager: { get_model: vi.fn() },
    };
    const proxy = modelProxy(model as never, controller.signal);
    const first = vi.fn();
    const second = vi.fn();

    proxy.on("change:value", first);
    proxy.on("change:value", second);
    proxy.off("change:value", first);
    proxy.off("change:value");

    expect(on).toHaveBeenCalledTimes(2);
    expect(add).toHaveBeenCalledTimes(2);
    expect(remove).toHaveBeenCalledTimes(2);
    expect(off).toHaveBeenCalledTimes(2);
    controller.abort();
    expect(off).toHaveBeenCalledTimes(2);
  });

  test("mounts through a registered publication loader", async () => {
    const url = moduleUrl(`
      export default {
        render({ el }) { el.dataset.registered = "true"; },
      };
    `);
    const loader = anyWidgetLoader();
    const output = await outputFor(
      payload({
        modelNotifications: [notification({ id: "model-0", state: {}, moduleUrl: url })],
      }),
      { loaders: [loader] },
    );
    const element = documentValue.createElement("div");

    const mounted = await output.mount(element as unknown as HTMLElement);

    expect(element.dataset.registered).toBe("true");
    await mounted.dispose();
  });

  test("keeps parent disposal authoritative over a child render signal", async () => {
    const counters = { childAbort: 0, childController: new AbortController() };
    vi.stubGlobal("__anywidgetChildSignal", counters);
    const rootUrl = moduleUrl(`
      export default {
        async render({ model, el, host }) {
          const child = await host.getWidget(model.get("child"));
          await child.render({
            el: document.createElement("div"),
            signal: globalThis.__anywidgetChildSignal.childController.signal,
          });
        },
      };
    `);
    const childUrl = moduleUrl(`
      export default {
        render({ signal }) {
          signal.addEventListener(
            "abort",
            () => globalThis.__anywidgetChildSignal.childAbort += 1,
            { once: true },
          );
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [
          notification({
            id: "model-0",
            state: { child: "anywidget:model-1" },
            moduleUrl: rootUrl,
          }),
          notification({ id: "model-1", state: {}, moduleUrl: childUrl }),
        ],
      }),
    );
    const mounted = await (
      await output.load(anyWidgetLoader())
    ).mount(documentValue.createElement("div") as unknown as HTMLElement);

    await mounted.dispose();

    expect(counters.childAbort).toBe(1);
    expect(counters.childController.signal.aborted).toBe(false);
  });

  test("releases a failed child view while its parent continues", async () => {
    const counters = { changes: 0 };
    vi.stubGlobal("__anywidgetFailedChild", counters);
    const rootUrl = moduleUrl(`
      export default {
        async render({ model, el, host }) {
          const child = await host.getWidget(model.get("child"));
          try {
            await child.render({ el: document.createElement("div") });
          } catch {
            el.dataset.caught = "true";
          }
        },
      };
    `);
    const childUrl = moduleUrl(`
      export default {
        render({ model }) {
          model.on("change:value", () => globalThis.__anywidgetFailedChild.changes += 1);
          throw new Error("child render failed");
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [
          notification({
            id: "model-0",
            state: { child: "anywidget:model-1", _css: ".root {}" },
            moduleUrl: rootUrl,
          }),
          notification({
            id: "model-1",
            state: { value: 1, _css: ".child {}" },
            moduleUrl: childUrl,
          }),
        ],
      }),
    );
    const loaded = await output.load(anyWidgetLoader<{ child: string }>());
    const element = documentValue.createElement("div");
    const mounted = await loaded.mount(element as unknown as HTMLElement);

    expect(element.dataset.caught).toBe("true");
    expect(documentValue.head.children.map((style) => style.textContent)).toEqual([".root {}"]);
    const child = await mounted.model.widget_manager.get_model<{ value: number }>("model-1");
    child.set("value", 2);
    expect(counters.changes).toBe(0);
    await mounted.dispose();
  });

  test("requires disposal before reusing a mount element", async () => {
    const url = moduleUrl("export default { render() {} };");
    const output = await outputFor(
      payload({
        modelNotifications: [notification({ id: "model-0", state: {}, moduleUrl: url })],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const element = documentValue.createElement("div") as unknown as HTMLElement;
    const first = await loaded.mount(element);

    await expect(loaded.mount(element)).rejects.toThrow("Dispose the existing AnyWidget mount");
    await first.dispose();
    const second = await loaded.mount(element);
    await second.dispose();
  });

  test("reports a cleanup failure that arrives after disposal", async () => {
    let releaseRender!: () => void;
    const renderGate = new Promise<void>((resolve) => {
      releaseRender = resolve;
    });
    let markStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    vi.stubGlobal("__anywidgetLateCleanup", { renderGate, markStarted });
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const url = moduleUrl(`
      export default {
        render() {
          globalThis.__anywidgetLateCleanup.markStarted();
          return globalThis.__anywidgetLateCleanup.renderGate.then(
            () => () => { throw new Error("late cleanup failed"); },
          );
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [notification({ id: "model-0", state: {}, moduleUrl: url })],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const controller = new AbortController();
    const mounting = loaded.mount(documentValue.createElement("div") as unknown as HTMLElement, {
      signal: controller.signal,
    });
    await started;

    controller.abort();
    await expect(mounting).rejects.toMatchObject({ name: "AbortError" });
    releaseRender();
    await renderGate;
    await vi.waitFor(() =>
      expect(consoleError).toHaveBeenCalledWith(
        "AnyWidget render settled after its mount was disposed.",
        expect.objectContaining({ message: "AnyWidget render cleanup failed." }),
      ),
    );
  });

  test("mounts a composed model graph and releases its browser resources", async () => {
    const counters: Counters = {
      rootInitialize: 0,
      rootRender: 0,
      rootRenderCleanup: 0,
      rootAbort: 0,
      childInitialize: 0,
      childInitializeCleanup: 0,
      childRender: 0,
      childRenderCleanup: 0,
      sendCallback: 0,
    };
    vi.stubGlobal("__anywidgetRuntimeCounters", counters);
    const rootUrl = moduleUrl(`
      export default {
        initialize({ model, signal }) {
          const counters = globalThis.__anywidgetRuntimeCounters;
          counters.rootInitialize += 1;
          signal.addEventListener("abort", () => counters.rootAbort += 1, { once: true });
          return { read: () => model.get("count") };
        },
        async render({ model, el, host }) {
          const counters = globalThis.__anywidgetRuntimeCounters;
          counters.rootRender += 1;
          const draw = () => el.dataset.count = String(model.get("count"));
          model.on("change:count", draw);
          draw();
          const childModel = await host.getModel(model.get("child"));
          el.dataset.childLabel = childModel.get("label");
          const child = await host.getWidget(model.get("child"));
          const childElement = document.createElement("section");
          el.append(childElement);
          await child.render({ el: childElement });
          return () => counters.rootRenderCleanup += 1;
        },
      };
    `);
    const childUrl = moduleUrl(`
      export default {
        initialize() {
          const counters = globalThis.__anywidgetRuntimeCounters;
          counters.childInitialize += 1;
          return () => counters.childInitializeCleanup += 1;
        },
        render({ model, el }) {
          const counters = globalThis.__anywidgetRuntimeCounters;
          counters.childRender += 1;
          el.dataset.label = model.get("label");
          return () => counters.childRenderCleanup += 1;
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [
          notification({
            id: "model-0",
            state: { count: 1, child: "anywidget:model-1", _css: ".root { color: red; }" },
            moduleUrl: rootUrl,
          }),
          notification({
            id: "model-1",
            state: { label: "nested", _css: ".child { color: blue; }" },
            moduleUrl: childUrl,
          }),
        ],
      }),
    );
    const loaded =
      await output.load(
        anyWidgetLoader<{ count: number; child: string; _css: string }, { read(): number }>(),
      );
    const root = documentValue.createElement("main");

    const mounted = await loaded.mount(root as unknown as HTMLElement);

    expect(counters.rootInitialize).toBe(1);
    expect(counters.rootRender).toBe(1);
    expect(counters.childInitialize).toBe(1);
    expect(counters.childRender).toBe(1);
    expect(mounted.exports.read()).toBe(1);
    expect(root.dataset.count).toBe("1");
    expect(root.dataset.childLabel).toBe("nested");
    expect((root.children[0] as FakeElement).dataset.label).toBe("nested");
    expect(documentValue.head.children.map((style) => style.textContent)).toEqual([
      ".root { color: red; }",
      ".child { color: blue; }",
    ]);

    mounted.model.set("count", 4);
    mounted.model.save_changes();
    expect(root.dataset.count).toBe("4");
    mounted.model.set("_css", ".root { color: green; }");
    expect(documentValue.head.children[0]?.textContent).toBe(".root { color: green; }");
    const childModel = await mounted.model.widget_manager.get_model<{ label: string }>("model-1");
    expect(childModel.get("label")).toBe("nested");
    mounted.model.send({}, () => {
      counters.sendCallback += 1;
    });
    await Promise.resolve();
    expect(counters.sendCallback).toBe(1);

    await mounted.dispose();
    await mounted.dispose();

    expect(counters.rootAbort).toBe(1);
    expect(counters.rootRenderCleanup).toBe(1);
    expect(counters.childInitializeCleanup).toBe(1);
    expect(counters.childRenderCleanup).toBe(1);
    expect(documentValue.head.children).toHaveLength(0);
    expect(root.children).toHaveLength(0);
  });

  test("destroys child bindings before their parent", async () => {
    const cleanupOrder: string[] = [];
    vi.stubGlobal("__anywidgetCleanupOrder", cleanupOrder);
    const rootUrl = moduleUrl(`
      export default {
        async render({ model, el, host }) {
          const child = await host.getWidget(model.get("child"));
          await child.render({ el: document.createElement("div") });
          return () => globalThis.__anywidgetCleanupOrder.push("root");
        },
      };
    `);
    const childUrl = moduleUrl(`
      export default {
        render() {
          return () => globalThis.__anywidgetCleanupOrder.push("child");
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [
          notification({
            id: "model-0",
            state: { child: "anywidget:model-1" },
            moduleUrl: rootUrl,
          }),
          notification({ id: "model-1", state: {}, moduleUrl: childUrl }),
        ],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const mounted = await loaded.mount(
      documentValue.createElement("div") as unknown as HTMLElement,
    );

    await mounted.dispose();

    expect(cleanupOrder).toEqual(["child", "root"]);
  });

  test("creates isolated state for each mount", async () => {
    const url = moduleUrl(`
      export default {
        render({ model, el }) {
          const draw = () => el.dataset.value = String(model.get("value"));
          model.on("change:value", draw);
          draw();
          el.dataset.byte = String(new Uint8Array(model.get("binary").view.buffer)[0]);
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [
          notification({
            id: "model-0",
            state: { value: 3, binary: {} },
            moduleUrl: url,
            bufferPaths: [["binary", "view"]],
            buffers: ["Bw=="],
          }),
        ],
      }),
    );
    const loaded =
      await output.load(anyWidgetLoader<{ value: number; binary: { view: DataView } }>());
    new Uint8Array(loaded.initialState.binary.view.buffer)[0] = 99;
    const firstElement = documentValue.createElement("div");
    const secondElement = documentValue.createElement("div");
    const first = await loaded.mount(firstElement as unknown as HTMLElement);
    const second = await loaded.mount(secondElement as unknown as HTMLElement);

    first.model.set("value", 9);

    expect(firstElement.dataset.value).toBe("9");
    expect(secondElement.dataset.value).toBe("3");
    expect(firstElement.dataset.byte).toBe("7");
    expect(secondElement.dataset.byte).toBe("7");
    expect(second.model.get("value")).toBe(3);
    new Uint8Array(first.model.get("binary").view.buffer)[0] = 12;
    expect(new Uint8Array(second.model.get("binary").view.buffer)[0]).toBe(7);
    await first.dispose();
    await second.dispose();
  });

  test("imports embedded ESM through a mount-owned object URL", async () => {
    const source = `export default { render({ el }) { el.dataset.embedded = "true"; } };`;
    const objectUrl = moduleUrl(source);
    const createObjectUrl = vi.spyOn(URL, "createObjectURL").mockReturnValue(objectUrl);
    const revokeObjectUrl = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const output = await outputFor(
      payload({
        files: { "/@file/widget.js": moduleUrl(source) },
        modelNotifications: [
          notification({ id: "model-0", state: {}, moduleUrl: "/@file/widget.js" }),
        ],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const element = documentValue.createElement("div");

    const mounted = await loaded.mount(element as unknown as HTMLElement);
    expect(element.dataset.embedded).toBe("true");
    expect(createObjectUrl).toHaveBeenCalledOnce();

    await mounted.dispose();

    expect(revokeObjectUrl).toHaveBeenCalledWith(objectUrl);
  });

  test("decodes an uppercase Base64 marker before importing embedded ESM", async () => {
    const source = `export default { render({ el }) { el.dataset.base64 = "true"; } };`;
    const objectUrl = moduleUrl(source);
    let embeddedModule: Blob | undefined;
    vi.spyOn(URL, "createObjectURL").mockImplementation((value) => {
      if (!(value instanceof Blob)) throw new TypeError("Expected an embedded module blob.");
      embeddedModule = value;
      return objectUrl;
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const output = await outputFor(
      payload({
        files: { "/@file/widget.js": base64ModuleUrl(source, "BASE64") },
        modelNotifications: [
          notification({ id: "model-0", state: {}, moduleUrl: "/@file/widget.js" }),
        ],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const element = documentValue.createElement("div");

    const mounted = await loaded.mount(element as unknown as HTMLElement);

    expect(element.dataset.base64).toBe("true");
    expect(await embeddedModule?.text()).toBe(source);
    await mounted.dispose();
  });

  test("keeps module cache entries distinct when hashes and URLs contain NUL", async () => {
    const firstSource = `export default { render({ el }) { el.dataset.module = "first"; } };`;
    const secondSource = `export default { render({ el }) { el.dataset.module = "second"; } };`;
    const moduleUrls = [moduleUrl(firstSource), moduleUrl(secondSource)];
    const createObjectUrl = vi
      .spyOn(URL, "createObjectURL")
      .mockImplementation(() => moduleUrls.shift()!);
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const firstVirtualFile = "b\0c";
    const secondVirtualFile = "c";
    const rootUrl = moduleUrl(`
      export default {
        async render({ model, el, host }) {
          const first = await host.getWidget(model.get("first"));
          const firstElement = document.createElement("div");
          el.append(firstElement);
          await first.render({ el: firstElement });
          const second = await host.getWidget(model.get("second"));
          const secondElement = document.createElement("div");
          el.append(secondElement);
          await second.render({ el: secondElement });
        },
      };
    `);
    const output = await outputFor(
      payload({
        files: {
          [firstVirtualFile]: moduleUrl(firstSource),
          [secondVirtualFile]: moduleUrl(secondSource),
        },
        modelNotifications: [
          notification({
            id: "model-0",
            state: { first: "anywidget:model-1", second: "anywidget:model-2" },
            moduleUrl: rootUrl,
          }),
          notification({
            id: "model-1",
            state: {},
            moduleUrl: firstVirtualFile,
            moduleHash: "a",
          }),
          notification({
            id: "model-2",
            state: {},
            moduleUrl: secondVirtualFile,
            moduleHash: "a\0b",
          }),
        ],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const element = documentValue.createElement("div");

    const mounted = await loaded.mount(element as unknown as HTMLElement);

    expect(createObjectUrl).toHaveBeenCalledTimes(2);
    expect(element.children.map((child) => child.dataset.module)).toEqual(["first", "second"]);
    await mounted.dispose();
  });

  test("does not start a child module after its parent render is disposed", async () => {
    let releaseRender!: () => void;
    const renderGate = new Promise<void>((resolve) => {
      releaseRender = resolve;
    });
    let markStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    let markSettled!: () => void;
    const settled = new Promise<void>((resolve) => {
      markSettled = resolve;
    });
    const counters = { childImports: 0, markSettled, markStarted, renderGate };
    vi.stubGlobal("__anywidgetDelayedChild", counters);
    const rootUrl = moduleUrl(`
      export default {
        async render({ model, host }) {
          const counters = globalThis.__anywidgetDelayedChild;
          counters.markStarted();
          await counters.renderGate;
          try {
            await host.getWidget(model.get("child"));
          } finally {
            counters.markSettled();
          }
        },
      };
    `);
    const childSource = `
      globalThis.__anywidgetDelayedChild.childImports += 1;
      export default { render() {} };
    `;
    const createObjectUrl = vi.spyOn(URL, "createObjectURL");
    const output = await outputFor(
      payload({
        files: { "/@file/child.js": moduleUrl(childSource) },
        modelNotifications: [
          notification({
            id: "model-0",
            state: { child: "anywidget:model-1" },
            moduleUrl: rootUrl,
          }),
          notification({ id: "model-1", state: {}, moduleUrl: "/@file/child.js" }),
        ],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const controller = new AbortController();
    const mounting = loaded.mount(documentValue.createElement("div") as unknown as HTMLElement, {
      signal: controller.signal,
    });
    await started;

    controller.abort();
    await expect(settleWithin(mounting)).rejects.toMatchObject({ name: "AbortError" });
    releaseRender();
    await settled;
    await Promise.resolve();

    expect(createObjectUrl).not.toHaveBeenCalled();
    expect(counters.childImports).toBe(0);
  });

  test("clears partial DOM when render throws synchronously", async () => {
    const url = moduleUrl(`
      export default {
        render({ el }) {
          el.append(document.createElement("span"));
          throw new Error("render failed");
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [notification({ id: "model-0", state: {}, moduleUrl: url })],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const element = documentValue.createElement("div");

    await expect(loaded.mount(element as unknown as HTMLElement)).rejects.toThrow("render failed");

    expect(element.children).toHaveLength(0);
  });

  test("clears DOM when a void renderer aborts during render", async () => {
    const controller = new AbortController();
    vi.stubGlobal("__anywidgetVoidAbort", controller);
    const url = moduleUrl(`
      export default {
        render({ el }) {
          el.append(document.createElement("span"));
          globalThis.__anywidgetVoidAbort.abort();
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [notification({ id: "model-0", state: {}, moduleUrl: url })],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const element = documentValue.createElement("div");

    await expect(
      loaded.mount(element as unknown as HTMLElement, { signal: controller.signal }),
    ).rejects.toMatchObject({ name: "AbortError" });

    expect(element.children).toHaveLength(0);
  });

  test("settles an aborted mount while module evaluation remains pending", async () => {
    let markStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    vi.stubGlobal("__anywidgetNeverImport", { markStarted });
    const url = moduleUrl(`
      globalThis.__anywidgetNeverImport.markStarted();
      await new Promise(() => {});
      export default { render() {} };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [notification({ id: "model-0", state: {}, moduleUrl: url })],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const controller = new AbortController();
    const mounting = loaded.mount(documentValue.createElement("div") as unknown as HTMLElement, {
      signal: controller.signal,
    });
    await started;

    controller.abort();

    await expect(settleWithin(mounting)).rejects.toMatchObject({ name: "AbortError" });
  });

  test("revokes an embedded module URL and skips late initialization after abort", async () => {
    let releaseModule!: () => void;
    const moduleGate = new Promise<void>((resolve) => {
      releaseModule = resolve;
    });
    let markStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    let markSettled!: () => void;
    const settled = new Promise<void>((resolve) => {
      markSettled = resolve;
    });
    const counters = { initialize: 0, render: 0, markStarted, markSettled };
    vi.stubGlobal("__anywidgetLateImport", counters);
    vi.stubGlobal("__anywidgetLateImportGate", moduleGate);
    const source = `
      globalThis.__anywidgetLateImport.markStarted();
      await globalThis.__anywidgetLateImportGate;
      globalThis.__anywidgetLateImport.markSettled();
      export default {
        initialize() { globalThis.__anywidgetLateImport.initialize += 1; },
        render() { globalThis.__anywidgetLateImport.render += 1; },
      };
    `;
    const objectUrl = moduleUrl(source);
    vi.spyOn(URL, "createObjectURL").mockReturnValue(objectUrl);
    const revokeObjectUrl = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const output = await outputFor(
      payload({
        files: { "/@file/late-widget.js": moduleUrl(source) },
        modelNotifications: [
          notification({ id: "model-0", state: {}, moduleUrl: "/@file/late-widget.js" }),
        ],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const controller = new AbortController();
    const mounting = loaded.mount(documentValue.createElement("div") as unknown as HTMLElement, {
      signal: controller.signal,
    });
    await started;

    controller.abort();

    await expect(settleWithin(mounting)).rejects.toMatchObject({ name: "AbortError" });
    expect(revokeObjectUrl).toHaveBeenCalledOnce();
    expect(revokeObjectUrl).toHaveBeenCalledWith(objectUrl);

    releaseModule();
    await settled;
    await Promise.resolve();

    expect(counters.initialize).toBe(0);
    expect(counters.render).toBe(0);
  });

  test("ties the mounted lifecycle to an external abort signal", async () => {
    const counters = { cleanup: 0 };
    vi.stubGlobal("__anywidgetAbortCounters", counters);
    const url = moduleUrl(`
      export default {
        render({ el }) {
          el.dataset.mounted = "true";
          return () => globalThis.__anywidgetAbortCounters.cleanup += 1;
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [notification({ id: "model-0", state: {}, moduleUrl: url })],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const element = documentValue.createElement("div");
    const controller = new AbortController();
    const mounted = await loaded.mount(element as unknown as HTMLElement, {
      signal: controller.signal,
    });

    controller.abort();
    await mounted.dispose();

    expect(counters.cleanup).toBe(1);
    expect(element.children).toHaveLength(0);
  });

  test("cancels an in-flight initialize and runs its late cleanup", async () => {
    let markStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    const counters = { initialize: 0, cleanup: 0, markStarted };
    vi.stubGlobal("__anywidgetPendingCounters", counters);
    const url = moduleUrl(`
      export default {
        initialize({ signal }) {
          const counters = globalThis.__anywidgetPendingCounters;
          counters.initialize += 1;
          counters.markStarted();
          return new Promise((resolve) => {
            signal.addEventListener(
              "abort",
              () => resolve(() => counters.cleanup += 1),
              { once: true },
            );
          });
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [notification({ id: "model-0", state: {}, moduleUrl: url })],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const controller = new AbortController();
    const mounting = loaded.mount(documentValue.createElement("div") as unknown as HTMLElement, {
      signal: controller.signal,
    });
    await started;

    controller.abort();

    await expect(mounting).rejects.toMatchObject({ name: "AbortError" });
    expect(counters.cleanup).toBe(1);
  });

  test("cancels an in-flight render and runs its late cleanup", async () => {
    let markStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    const counters = { render: 0, cleanup: 0, markStarted };
    vi.stubGlobal("__anywidgetPendingRenderCounters", counters);
    const url = moduleUrl(`
      export default {
        render({ signal }) {
          const counters = globalThis.__anywidgetPendingRenderCounters;
          counters.render += 1;
          counters.markStarted();
          return new Promise((resolve) => {
            signal.addEventListener(
              "abort",
              () => resolve(() => counters.cleanup += 1),
              { once: true },
            );
          });
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [notification({ id: "model-0", state: {}, moduleUrl: url })],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const controller = new AbortController();
    const mounting = loaded.mount(documentValue.createElement("div") as unknown as HTMLElement, {
      signal: controller.signal,
    });
    await started;

    controller.abort();

    await expect(mounting).rejects.toMatchObject({ name: "AbortError" });
    expect(counters.cleanup).toBe(1);
  });

  test("aborts the binding signal when initialization fails", async () => {
    const counters = { abort: 0 };
    vi.stubGlobal("__anywidgetFailedInitializeCounters", counters);
    const url = moduleUrl(`
      export default {
        initialize({ signal }) {
          const counters = globalThis.__anywidgetFailedInitializeCounters;
          signal.addEventListener("abort", () => counters.abort += 1, { once: true });
          throw new Error("initialize failed");
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [notification({ id: "model-0", state: {}, moduleUrl: url })],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());

    await expect(
      loaded.mount(documentValue.createElement("div") as unknown as HTMLElement),
    ).rejects.toThrow("initialize failed");
    expect(counters.abort).toBe(1);
  });

  test("isolates model listener failures from sibling and aggregate change events", async () => {
    const counters = { sibling: 0, change: 0 };
    vi.stubGlobal("__anywidgetListenerCounters", counters);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const url = moduleUrl(`
      export default {
        render({ model }) {
          model.on("change:value", () => { throw new Error("listener failed"); });
          model.on("change:value", () => globalThis.__anywidgetListenerCounters.sibling += 1);
          model.on("change", () => globalThis.__anywidgetListenerCounters.change += 1);
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [notification({ id: "model-0", state: { value: 1 }, moduleUrl: url })],
      }),
    );
    const loaded = await output.load(anyWidgetLoader<{ value: number }>());
    const mounted = await loaded.mount(
      documentValue.createElement("div") as unknown as HTMLElement,
    );

    expect(() => mounted.model.set("value", 2)).not.toThrow();
    expect(counters.sibling).toBe(1);
    await Promise.resolve();
    expect(counters.change).toBe(1);
    expect(consoleError).toHaveBeenCalledWith(
      'AnyWidget model listener for "change:value" failed.',
      expect.objectContaining({ message: "listener failed" }),
    );

    await mounted.dispose();
  });

  test("resolves legacy IPython model references through widget_manager", async () => {
    const rootUrl = moduleUrl(`
      export default {
        async render({ model, el }) {
          const child = await model.widget_manager.get_model(model.get("child"));
          el.dataset.childLabel = child.get("label");
        },
      };
    `);
    const output = await outputFor(
      payload({
        modelNotifications: [
          notification({
            id: "model-0",
            state: { child: "IPY_MODEL_model-1" },
            moduleUrl: rootUrl,
          }),
          notification({ id: "model-1", state: { label: "legacy child" } }),
        ],
      }),
    );
    const loaded = await output.load(anyWidgetLoader<{ child: string }>());
    const element = documentValue.createElement("div");

    const mounted = await loaded.mount(element as unknown as HTMLElement);

    expect(element.dataset.childLabel).toBe("legacy child");
    await mounted.dispose();
  });

  test("shares model styles within a shadow root and releases the final reference", async () => {
    const rootUrl = moduleUrl(`
      export default {
        async render({ model, el, host }) {
          const child = await host.getWidget(model.get("child"));
          for (let index = 0; index < 2; index += 1) {
            const childElement = document.createElement("section");
            el.append(childElement);
            await child.render({ el: childElement });
          }
        },
      };
    `);
    const childUrl = moduleUrl("export default { render() {} };");
    const output = await outputFor(
      payload({
        modelNotifications: [
          notification({
            id: "model-0",
            state: { child: "anywidget:model-1", _css: ".root {}" },
            moduleUrl: rootUrl,
          }),
          notification({
            id: "model-1",
            state: { _css: ".child {}" },
            moduleUrl: childUrl,
          }),
        ],
      }),
    );
    const loaded = await output.load(anyWidgetLoader());
    const host = documentValue.createElement("aside");
    const shadow = new FakeShadowRoot(documentValue, host);
    const element = documentValue.createElement("main");
    shadow.append(element);

    const mounted = await loaded.mount(element as unknown as HTMLElement);

    expect(shadow.children.filter((child) => child.tagName === "STYLE")).toHaveLength(2);
    expect(
      shadow.children
        .filter((child) => child.tagName === "STYLE")
        .map((style) => style.textContent),
    ).toEqual([".root {}", ".child {}"]);

    await mounted.dispose();

    expect(shadow.children.filter((child) => child.tagName === "STYLE")).toHaveLength(0);
  });
});

async function settleWithin<T>(task: Promise<T>): Promise<T> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  const expired = new Promise<never>((_resolve, reject) => {
    timeout = setTimeout(() => reject(new Error("AnyWidget lifecycle did not settle.")), 250);
  });
  try {
    return await Promise.race([task, expired]);
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
  }
}

class FakeElement {
  readonly nodeType = 1;
  readonly children: FakeElement[] = [];
  readonly dataset: Record<string, string> = {};
  readonly ownerDocument: FakeDocument;
  readonly tagName: string;
  parent: FakeElement | FakeShadowRoot | undefined;
  #textContent = "";

  constructor(ownerDocument: FakeDocument, tagName: string) {
    this.ownerDocument = ownerDocument;
    this.tagName = tagName.toUpperCase();
  }

  get textContent(): string {
    return this.#textContent;
  }

  set textContent(value: string) {
    this.#textContent = value;
    for (const child of this.children) child.parent = undefined;
    this.children.splice(0);
  }

  getRootNode(): FakeDocument | FakeShadowRoot {
    return this.parent?.getRootNode() ?? this.ownerDocument;
  }

  append(...children: FakeElement[]): void {
    for (const child of children) {
      child.parent = this;
      this.children.push(child);
    }
  }

  replaceChildren(...children: FakeElement[]): void {
    this.#textContent = "";
    for (const child of this.children) child.parent = undefined;
    this.children.splice(0);
    this.append(...children);
  }

  remove(): void {
    if (this.parent === undefined) return;
    const index = this.parent.children.indexOf(this);
    if (index >= 0) this.parent.children.splice(index, 1);
    this.parent = undefined;
  }
}

class FakeShadowRoot {
  readonly nodeType = 11;
  readonly children: FakeElement[] = [];

  constructor(
    readonly ownerDocument: FakeDocument,
    readonly host: FakeElement,
  ) {}

  append(...children: FakeElement[]): void {
    for (const child of children) {
      child.parent = this;
      this.children.push(child);
    }
  }

  getRootNode(): FakeShadowRoot {
    return this;
  }
}

class FakeDocument {
  readonly nodeType = 9;
  readonly head = new FakeElement(this, "head");

  createElement(tagName: string): FakeElement {
    return new FakeElement(this, tagName);
  }
}
