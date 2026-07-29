import { afterEach, describe, expect, test, vi } from "vite-plus/test";

import { anyWidgetLoader } from "../src/index.js";
import { moduleUrl, notification, outputFor, payload } from "./fixture.js";

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
    const output = await outputFor(
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
    const loaded =
      await output.load(
        anyWidgetLoader<{ count: number; child: string; _css: string }, { read(): number }>(),
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
});

function widgetStyles(): string[] {
  return [...document.head.querySelectorAll("style")]
    .map((style) => style.textContent ?? "")
    .filter((css) => css === ROOT_CSS || css === UPDATED_ROOT_CSS || css === CHILD_CSS);
}
