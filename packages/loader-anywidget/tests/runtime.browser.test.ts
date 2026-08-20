import { afterEach, describe, expect, test, vi } from "vite-plus/test";

import { loadPayload, moduleUrl, notification, payload } from "./fixture.js";

interface RuntimeCounters {
  rootInitialize: number;
  rootRender: number;
  rootRenderCleanup: number;
  rootAbort: number;
  childInitialize: number;
  childInitializeCleanup: number;
  childRender: number;
  childRenderCleanup: number;
}

const COUNTERS = "__marimoExportAnyWidgetBrowserCounters";
const MODULE_EVALUATIONS = "__marimoExportAnyWidgetModuleEvaluations";
const PENDING_IMPORT = "__marimoExportAnyWidgetPendingImport";
const PENDING_IMPORT_GATE = "__marimoExportAnyWidgetPendingImportGate";
const RETRY_IMPORT = "__marimoExportAnyWidgetRetryImport";
const DUAL_PENDING_IMPORT = "__marimoExportAnyWidgetDualPendingImport";
const DUAL_PENDING_GATE = "__marimoExportAnyWidgetDualPendingGate";
const DUAL_RETRY_IMPORT = "__marimoExportAnyWidgetDualRetryImport";
const ROOT_CSS = '[data-anywidget-root="true"] { color: rgb(17, 34, 51); }';
const UPDATED_ROOT_CSS = '[data-anywidget-root="true"] { color: rgb(85, 102, 119); }';
const CHILD_CSS = '[data-anywidget-child="true"] { color: rgb(51, 68, 85); }';

let host: HTMLElement | undefined;
let disposeMount: (() => Promise<void>) | undefined;

afterEach(async () => {
  await disposeMount?.();
  disposeMount = undefined;
  host?.remove();
  host = undefined;
  Reflect.deleteProperty(globalThis, COUNTERS);
  Reflect.deleteProperty(globalThis, MODULE_EVALUATIONS);
  Reflect.deleteProperty(globalThis, PENDING_IMPORT);
  Reflect.deleteProperty(globalThis, PENDING_IMPORT_GATE);
  Reflect.deleteProperty(globalThis, RETRY_IMPORT);
  Reflect.deleteProperty(globalThis, DUAL_PENDING_IMPORT);
  Reflect.deleteProperty(globalThis, DUAL_PENDING_GATE);
  Reflect.deleteProperty(globalThis, DUAL_RETRY_IMPORT);
  vi.restoreAllMocks();
});

describe("AnyWidget native browser runtime", () => {
  test("imports embedded modules, renders a model graph, and releases browser resources", async () => {
    const counters: RuntimeCounters = {
      rootInitialize: 0,
      rootRender: 0,
      rootRenderCleanup: 0,
      rootAbort: 0,
      childInitialize: 0,
      childInitializeCleanup: 0,
      childRender: 0,
      childRenderCleanup: 0,
    };
    Reflect.set(globalThis, COUNTERS, counters);

    const originalCreateObjectURL = URL.createObjectURL.bind(URL);
    const originalRevokeObjectURL = URL.revokeObjectURL.bind(URL);
    const createdObjectUrls: string[] = [];
    const createdBlobs: Blob[] = [];
    const revokedObjectUrls: string[] = [];
    vi.spyOn(URL, "createObjectURL").mockImplementation((object) => {
      if (object instanceof Blob) createdBlobs.push(object);
      const url = originalCreateObjectURL(object);
      createdObjectUrls.push(url);
      return url;
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation((url) => {
      revokedObjectUrls.push(url);
      originalRevokeObjectURL(url);
    });

    const rootSource = `
      export default {
        initialize({ model, signal }) {
          const counters = globalThis.${COUNTERS};
          counters.rootInitialize += 1;
          signal.addEventListener("abort", () => counters.rootAbort += 1, { once: true });
          return { read: () => model.get("count") };
        },
        async render({ model, el, host, signal }) {
          const counters = globalThis.${COUNTERS};
          counters.rootRender += 1;
          el.dataset.anywidgetRoot = "true";

          const button = document.createElement("button");
          button.type = "button";
          button.textContent = "Increment";
          const value = document.createElement("output");
          value.dataset.testid = "count";
          const draw = () => value.textContent = String(model.get("count"));
          model.on("change:count", draw);
          button.addEventListener(
            "click",
            () => model.set("count", model.get("count") + 1),
            { signal },
          );
          draw();
          el.append(button, value);

          const child = await host.getWidget(model.get("child"));
          const childElement = document.createElement("section");
          childElement.dataset.testid = "child";
          el.append(childElement);
          await child.render({ el: childElement });
          return () => counters.rootRenderCleanup += 1;
        },
      };
    `;
    const childSource = `
      export default {
        initialize() {
          const counters = globalThis.${COUNTERS};
          counters.childInitialize += 1;
          return () => counters.childInitializeCleanup += 1;
        },
        render({ model, el }) {
          const counters = globalThis.${COUNTERS};
          counters.childRender += 1;
          el.dataset.anywidgetChild = "true";
          el.textContent = model.get("label");
          return () => counters.childRenderCleanup += 1;
        },
      };
    `;
    const loaded = await loadPayload<
      { count: number; child: string; _css: string },
      { read(): number }
    >(
      payload({
        files: {
          "/@file/root.js": moduleUrl(rootSource),
          "/@file/child.js": moduleUrl(childSource),
        },
        modelNotifications: [
          notification({
            id: "model-0",
            state: {
              count: 1,
              child: "anywidget:model-1",
              _css: ROOT_CSS,
            },
            moduleUrl: "/@file/root.js",
          }),
          notification({
            id: "model-1",
            state: { label: "nested", _css: CHILD_CSS },
            moduleUrl: "/@file/child.js",
          }),
        ],
      }),
    );
    host = document.createElement("main");
    document.body.append(host);

    const mounted = await loaded.mount(host);
    disposeMount = () => mounted.dispose();

    expect(counters.rootInitialize).toBe(1);
    expect(counters.rootRender).toBe(1);
    expect(counters.childInitialize).toBe(1);
    expect(counters.childRender).toBe(1);
    expect(mounted.exports.read()).toBe(1);
    expect(host.querySelector<HTMLOutputElement>('[data-testid="count"]')?.value).toBe("1");
    const child = host.querySelector<HTMLElement>('[data-testid="child"]');
    expect(child?.textContent).toBe("nested");
    expect(createdObjectUrls).toHaveLength(2);
    expect(createdBlobs.map((blob) => blob.type)).toEqual(["text/javascript", "text/javascript"]);
    expect(widgetStyles()).toEqual(expect.arrayContaining([ROOT_CSS, CHILD_CSS]));
    expect(getComputedStyle(host).color).toBe("rgb(17, 34, 51)");
    expect(getComputedStyle(child!).color).toBe("rgb(51, 68, 85)");

    const button = host.querySelector<HTMLButtonElement>("button");
    expect(button).not.toBeNull();
    button!.click();
    await vi.waitFor(() => {
      expect(host?.querySelector<HTMLOutputElement>('[data-testid="count"]')?.value).toBe("2");
    });
    expect(mounted.model.get("count")).toBe(2);
    expect(mounted.exports.read()).toBe(2);
    mounted.model.save_changes();

    mounted.model.set("_css", UPDATED_ROOT_CSS);
    await vi.waitFor(() => {
      expect(getComputedStyle(host!).color).toBe("rgb(85, 102, 119)");
    });

    let sendCallbacks = 0;
    mounted.model.send({}, () => {
      sendCallbacks += 1;
    });
    await vi.waitFor(() => {
      expect(sendCallbacks).toBe(1);
    });

    await mounted.dispose();
    await mounted.dispose();
    disposeMount = undefined;

    expect(counters.rootAbort).toBe(1);
    expect(counters.rootRenderCleanup).toBe(1);
    expect(counters.childInitializeCleanup).toBe(1);
    expect(counters.childRenderCleanup).toBe(1);
    expect(host.children).toHaveLength(0);
    expect(widgetStyles()).toHaveLength(0);
    expect([...revokedObjectUrls].sort()).toEqual([...createdObjectUrls].sort());
  });

  test("shares one embedded module evaluation across concurrent and sequential mounts", async () => {
    Reflect.set(globalThis, MODULE_EVALUATIONS, 0);
    const source = `
      globalThis.${MODULE_EVALUATIONS} += 1;
      export default {
        render({ model, el }) { el.textContent = String(model.get("count")); },
      };
    `;
    const loaded = await loadPayload<{ count: number }>(
      payload({
        files: { "/@file/shared.js": moduleUrl(source) },
        modelNotifications: [
          notification({
            id: "model-0",
            state: { count: 1 },
            moduleUrl: "/@file/shared.js",
            moduleHash: "shared-browser-module-v1",
          }),
        ],
      }),
    );
    const firstHost = document.createElement("section");
    const secondHost = document.createElement("section");
    const thirdHost = document.createElement("section");
    document.body.append(firstHost, secondHost, thirdHost);

    const [first, second] = await Promise.all([loaded.mount(firstHost), loaded.mount(secondHost)]);
    try {
      expect(Reflect.get(globalThis, MODULE_EVALUATIONS)).toBe(1);
      expect(first.model).not.toBe(second.model);
      first.model.set("count", 2);
      expect(second.model.get("count")).toBe(1);

      await first.dispose();
      await second.dispose();
      const third = await loaded.mount(thirdHost);
      try {
        expect(Reflect.get(globalThis, MODULE_EVALUATIONS)).toBe(1);
        expect(thirdHost.textContent).toBe("1");
      } finally {
        await third.dispose();
      }
    } finally {
      await first.dispose();
      await second.dispose();
      firstHost.remove();
      secondHost.remove();
      thirdHost.remove();
    }
  });

  test("disposing one mount preserves a shared pending module import", async () => {
    let markStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    let releaseImport!: () => void;
    const gate = new Promise<void>((resolve) => {
      releaseImport = resolve;
    });
    Reflect.set(globalThis, PENDING_IMPORT, { evaluations: 0, markStarted });
    Reflect.set(globalThis, PENDING_IMPORT_GATE, gate);
    const source = `
      globalThis.${PENDING_IMPORT}.evaluations += 1;
      globalThis.${PENDING_IMPORT}.markStarted();
      await globalThis.${PENDING_IMPORT_GATE};
      export default { render({ el }) { el.textContent = "ready"; } };
    `;
    const loaded = await loadPayload(
      payload({
        files: { "/@file/pending.js": moduleUrl(source) },
        modelNotifications: [
          notification({
            id: "model-0",
            state: {},
            moduleUrl: "/@file/pending.js",
            moduleHash: "pending-browser-module-v1",
          }),
        ],
      }),
    );
    const firstHost = document.createElement("section");
    const secondHost = document.createElement("section");
    document.body.append(firstHost, secondHost);
    const controller = new AbortController();
    const firstMount = loaded.mount(firstHost, { signal: controller.signal });
    const secondMount = loaded.mount(secondHost);

    await started;
    controller.abort();
    await expect(firstMount).rejects.toMatchObject({ name: "AbortError" });

    releaseImport();
    const second = await secondMount;
    try {
      expect(secondHost.textContent).toBe("ready");
      expect(Reflect.get(globalThis, PENDING_IMPORT)).toMatchObject({ evaluations: 1 });
    } finally {
      await second.dispose();
      firstHost.remove();
      secondHost.remove();
    }
  });

  test("retries an embedded module after evaluation rejects", async () => {
    Reflect.set(globalThis, RETRY_IMPORT, 0);
    const source = `
      globalThis.${RETRY_IMPORT} += 1;
      if (globalThis.${RETRY_IMPORT} === 1) throw new Error("first module evaluation failed");
      export default { render({ el }) { el.textContent = "retried"; } };
    `;
    const loaded = await loadPayload(
      payload({
        files: { "/@file/retry.js": moduleUrl(source) },
        modelNotifications: [
          notification({
            id: "model-0",
            state: {},
            moduleUrl: "/@file/retry.js",
            moduleHash: "retry-browser-module-v1",
          }),
        ],
      }),
    );
    const firstHost = document.createElement("section");
    const secondHost = document.createElement("section");
    document.body.append(firstHost, secondHost);

    await expect(loaded.mount(firstHost)).rejects.toThrow("first module evaluation failed");
    const mounted = await loaded.mount(secondHost);
    try {
      expect(Reflect.get(globalThis, RETRY_IMPORT)).toBe(2);
      expect(secondHost.textContent).toBe("retried");
    } finally {
      await mounted.dispose();
      firstHost.remove();
      secondHost.remove();
    }
  });

  test("shares pending and sequential imports across module-cache copies", async () => {
    const [firstCache, secondCache] = await moduleCacheCopies();
    let markStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    let releaseImport!: () => void;
    const gate = new Promise<void>((resolve) => {
      releaseImport = resolve;
    });
    Reflect.set(globalThis, DUAL_PENDING_IMPORT, { evaluations: 0, markStarted });
    Reflect.set(globalThis, DUAL_PENDING_GATE, gate);
    const source = `
      globalThis.${DUAL_PENDING_IMPORT}.evaluations += 1;
      globalThis.${DUAL_PENDING_IMPORT}.markStarted();
      await globalThis.${DUAL_PENDING_GATE};
      export default { render() {} };
    `;
    const spec = {
      hash: "dual-pending-browser-module-v1",
      url: "/@file/dual-pending.js",
    };
    const files = { "/@file/dual-pending.js": moduleUrl(source) };

    const first = firstCache.loadPageAnyWidget(spec, files);
    const second = secondCache.loadPageAnyWidget(spec, files);
    await started;
    releaseImport();
    const [firstWidget, secondWidget] = await Promise.all([first, second]);

    expect(firstWidget).toBe(secondWidget);
    expect(Reflect.get(globalThis, DUAL_PENDING_IMPORT)).toMatchObject({ evaluations: 1 });
    expect(await secondCache.loadPageAnyWidget(spec, files)).toBe(firstWidget);
    expect(Reflect.get(globalThis, DUAL_PENDING_IMPORT)).toMatchObject({ evaluations: 1 });
  });

  test("shares rejection eviction and retry across module-cache copies", async () => {
    const [firstCache, secondCache] = await moduleCacheCopies();
    Reflect.set(globalThis, DUAL_RETRY_IMPORT, 0);
    const source = `
      globalThis.${DUAL_RETRY_IMPORT} += 1;
      if (globalThis.${DUAL_RETRY_IMPORT} === 1) throw new Error("dual import failed");
      export default { render() {} };
    `;
    const spec = { hash: "dual-retry-browser-module-v1", url: "/@file/dual-retry.js" };
    const files = { "/@file/dual-retry.js": moduleUrl(source) };

    const failures = await Promise.allSettled([
      firstCache.loadPageAnyWidget(spec, files),
      secondCache.loadPageAnyWidget(spec, files),
    ]);
    expect(failures.map((result) => result.status)).toEqual(["rejected", "rejected"]);
    expect(Reflect.get(globalThis, DUAL_RETRY_IMPORT)).toBe(1);

    await secondCache.loadPageAnyWidget(spec, files);
    expect(Reflect.get(globalThis, DUAL_RETRY_IMPORT)).toBe(2);
  });

  test("shares the page admission cap across module-cache copies", async () => {
    const [firstCache, secondCache] = await moduleCacheCopies();
    const record = Reflect.get(globalThis, firstCache.PAGE_MODULE_CACHE_SYMBOL) as {
      readonly version: number;
      readonly modules: Map<string, Promise<unknown>>;
    };
    expect(record.version).toBe(1);
    expect(record.modules).toBeInstanceOf(Map);
    const added: string[] = [];
    while (record.modules.size < firstCache.MAX_PAGE_MODULES) {
      const key = `dual-cap-fixture-${added.length}`;
      record.modules.set(key, Promise.resolve({ render() {} }));
      added.push(key);
    }
    const spec = { hash: "dual-cap-browser-module-v1", url: "/@file/dual-cap.js" };
    const files = { "/@file/dual-cap.js": moduleUrl("export default { render() {} };") };

    try {
      await expect(firstCache.loadPageAnyWidget(spec, files)).rejects.toThrow(
        `${firstCache.MAX_PAGE_MODULES} unique modules`,
      );
      await expect(secondCache.loadPageAnyWidget(spec, files)).rejects.toThrow(
        `${secondCache.MAX_PAGE_MODULES} unique modules`,
      );
    } finally {
      for (const key of added) record.modules.delete(key);
    }
  });
});

async function moduleCacheCopies() {
  const [first, second] = await Promise.all([
    // @ts-expect-error Vite query parameters create independent browser module instances.
    import("../src/runtime/module-cache.ts?copy=first"),
    // @ts-expect-error Vite query parameters create independent browser module instances.
    import("../src/runtime/module-cache.ts?copy=second"),
  ]);
  return [first, second] as const;
}

function widgetStyles(): string[] {
  return [...document.head.querySelectorAll("style")]
    .map((style) => style.textContent ?? "")
    .filter((css) => css === ROOT_CSS || css === UPDATED_ROOT_CSS || css === CHILD_CSS);
}
