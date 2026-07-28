import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { extname, relative, resolve, sep } from "node:path";

import { chromium } from "playwright";

/* oxlint-disable eslint/no-await-in-loop */

const EXPECTED_STATES = [
  "baseline",
  "compact",
  "focus",
  "narrow_universe",
  "short_window",
  "weekly",
];
const EXPECTED_PATCH_KEYS = {
  baseline: [],
  compact: ["chart_width"],
  focus: ["symbols_selector"],
  narrow_universe: ["symbols", "symbols_selector"],
  short_window: ["end", "start"],
  weekly: ["interval"],
};
const PUBLICATIONS = [
  ["capture", "cold"],
  ["capture", "warm"],
  ["build", "cold"],
  ["build", "warm"],
];
const FORBIDDEN_PATH =
  /(?:\/api\/|\/kernel(?:\/|$)|\/sessions?(?:\/|$)|\/events?(?:\/|$)|\/sse(?:\/|$)|\.whl(?:$|\?)|wheel|websocket)/iu;

const workdir = resolve(argument("--workdir"));
const staticRoot = resolve(workdir, "browser", "static");
const acceptancePath = resolve(workdir, "acceptance.json");
const screenshotsDirectory = resolve(workdir, "browser", "screenshots");
const networkPath = resolve(workdir, "browser", "network.json");
const acceptance = JSON.parse(await readFile(acceptancePath, "utf8"));
assert.equal(acceptance.stage, "browser_pending", "Python acceptance is not browser-ready");
assert.equal(pythonProcesses(workdir), 0, "a workspace Python process is still running");
await stat(resolve(staticRoot, "index.html"));
await mkdir(screenshotsDirectory, { recursive: true });

const serverRequests = [];
const server = createStaticServer(staticRoot, serverRequests);
await new Promise((resolvePromise, reject) => {
  server.once("error", reject);
  server.listen(0, "127.0.0.1", resolvePromise);
});
const address = server.address();
assert(address !== null && typeof address === "object");
const origin = `http://127.0.0.1:${address.port}`;

let browser;
const consoleErrors = [];
const pageErrors = [];
const failedRequests = [];
const browserRequests = [];
const screenshots = [];
const stateEvidence = [];
let interactionEvidence;
let rapidEvidence;
let relocationEvidence;
let integrityEvidence;
let isolationEvidence;

try {
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => {
    const active = new Set();
    let created = 0;
    let revoked = 0;
    const create = URL.createObjectURL.bind(URL);
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (value) => {
      const url = create(value);
      active.add(url);
      created += 1;
      return url;
    };
    URL.revokeObjectURL = (url) => {
      if (active.delete(url)) revoked += 1;
      revoke(url);
    };
    Object.defineProperty(window, "__MARIMO_EXPORT_OBJECT_URLS__", {
      value: {
        get active() {
          return active.size;
        },
        get created() {
          return created;
        },
        get revoked() {
          return revoked;
        },
      },
    });
  });
  context.on("request", (request) => {
    browserRequests.push({
      method: request.method(),
      resourceType: request.resourceType(),
      url: request.url(),
    });
  });
  context.on("requestfailed", (request) => {
    failedRequests.push({
      error: request.failure()?.errorText ?? "unknown",
      url: request.url(),
    });
  });

  const page = await context.newPage();
  observePage(page, "main", consoleErrors, pageErrors);
  await page.goto(`${origin}/`, { waitUntil: "domcontentloaded" });

  for (const [ownership, run] of PUBLICATIONS) {
    const key = `${ownership}-${run}`;
    await choosePublication(page, ownership, run);
    let baselineWidth;
    let compactWidth;
    for (const state of EXPECTED_STATES) {
      const patch = await clickSparseState(page, state);
      assert.deepEqual(
        Object.keys(patch).sort(),
        EXPECTED_PATCH_KEYS[state],
        `${key}/${state} sparse patch changed`,
      );
      const evidence = await inspectReadyState(page, key, state);
      stateEvidence.push(evidence);
      if (state === "baseline") baselineWidth = evidence.png.width;
      if (state === "compact") compactWidth = evidence.png.width;
    }
    assert(
      Number.isInteger(baselineWidth) &&
        Number.isInteger(compactWidth) &&
        compactWidth < baselineWidth,
      `${key} compact PNG width did not decrease`,
    );
  }

  await choosePublication(page, "capture", "cold");
  await clickSparseState(page, "baseline");
  await inspectReadyState(page, "capture-cold", "baseline");
  interactionEvidence = await exerciseLocalInteractions(page, serverRequests);

  await captureSurfaces(page, screenshotsDirectory, screenshots);

  const beforeUnavailable = serverRequests.length;
  await page.locator("#unavailable-state").click();
  await page.locator('#unavailable-panel[data-error-code="state_unavailable"]').waitFor({
    state: "visible",
  });
  await page.waitForTimeout(200);
  assert.equal(
    serverRequests.length,
    beforeUnavailable,
    "unavailable vector triggered an asset request",
  );

  rapidEvidence = await exerciseRapidTransitions(page);
  const urlEvidence = await page.evaluate(() => {
    const value = window.__MARIMO_EXPORT_OBJECT_URLS__;
    return { active: value.active, created: value.created, revoked: value.revoked };
  });
  assert(urlEvidence.created > urlEvidence.active, "state replacement created no disposable URLs");
  assert.equal(
    urlEvidence.created - urlEvidence.revoked,
    urlEvidence.active,
    "object URL accounting is unbalanced",
  );
  assert(urlEvidence.active <= 3, "replaced mounts retained object URLs");

  const relocationPage = await context.newPage();
  observePage(relocationPage, "relocation", consoleErrors, pageErrors);
  await relocationPage.goto(
    `${origin}/?publication=${encodeURIComponent("./relocated/deep/finance/")}`,
    { waitUntil: "domcontentloaded" },
  );
  relocationEvidence = await inspectReadyState(relocationPage, "custom-publication", "baseline");
  assert(
    serverRequests.some(({ path }) => path === "/relocated/deep/finance/index.json"),
    "relocated index was not resolved from its nested base",
  );
  await relocationPage.close();

  const tamperedPage = await context.newPage();
  observePage(tamperedPage, "tampered", consoleErrors, pageErrors);
  await tamperedPage.goto(`${origin}/?publication=${encodeURIComponent("./tampered/")}`, {
    waitUntil: "domcontentloaded",
  });
  await tamperedPage
    .locator('#error-panel[data-error-code="integrity_failed"]')
    .waitFor({ state: "visible" });
  integrityEvidence = {
    error: await tamperedPage.locator("#error-message").textContent(),
    verification: await tamperedPage.locator("#verification").textContent(),
  };
  assert.match(integrityEvidence.error ?? "", /SHA-256|integrity/iu);
  await tamperedPage.close();

  const isolationStart = browserRequests.length;
  const isolationPage = await context.newPage();
  observePage(isolationPage, "arrow-only", consoleErrors, pageErrors);
  await isolationPage.goto(
    `${origin}/arrow-only.html?publication=${encodeURIComponent("./publications/capture-cold/")}`,
    { waitUntil: "domcontentloaded" },
  );
  await isolationPage.locator('body[data-status="ready"]').waitFor();
  const isolationRequests = browserRequests.slice(isolationStart);
  const isolationScripts = isolationRequests
    .map(({ url }) => new URL(url))
    .filter((url) => url.origin === origin && url.pathname.endsWith(".js"))
    .map((url) => url.pathname);
  assert(isolationScripts.length > 0, "Arrow-only entry loaded no JavaScript");
  assert(
    isolationScripts.every((path) => !path.includes("/embed-") && !path.includes("/index-")),
    "Arrow-only entry loaded a full-client or Vega chunk",
  );
  for (const path of isolationScripts) {
    const source = await readFile(resolveStaticPath(staticRoot, path), "utf8");
    assert.doesNotMatch(source, /anywidget|vega-embed|hyparquet/iu);
  }
  isolationEvidence = {
    scripts: isolationScripts,
    status: await isolationPage.locator("#arrow-only-status").textContent(),
  };
  await isolationPage.close();

  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent("pagehide"));
  });
  await page.waitForFunction(() => {
    const diagnostics = window.__MARIMO_EXPORT_DEMO__;
    const urls = window.__MARIMO_EXPORT_OBJECT_URLS__;
    return (
      diagnostics !== undefined &&
      diagnostics.mounts === diagnostics.disposals &&
      urls !== undefined &&
      urls.active === 0
    );
  });
  const finalDiagnostics = await page.evaluate(() => ({
    diagnostics: window.__MARIMO_EXPORT_DEMO__,
    urls: window.__MARIMO_EXPORT_OBJECT_URLS__,
  }));
  assert.equal(finalDiagnostics.diagnostics.errors, 0);
  await page.close();
  await context.close();

  const httpRequests = browserRequests.filter(({ url }) => /^https?:/u.test(url));
  const externalRequests = httpRequests.filter(({ url }) => new URL(url).origin !== origin);
  assert.deepEqual(externalRequests, [], "browser contacted an external HTTP origin");
  const pythonEndpointRequests = httpRequests.filter(({ url }) =>
    FORBIDDEN_PATH.test(new URL(url).pathname),
  );
  assert.deepEqual(pythonEndpointRequests, [], "browser contacted a serverful endpoint");
  assert.deepEqual(consoleErrors, [], `browser console errors: ${JSON.stringify(consoleErrors)}`);
  assert.deepEqual(pageErrors, [], `browser page errors: ${JSON.stringify(pageErrors)}`);
  assert(
    failedRequests.every(({ error }) => /aborted|cancelled/iu.test(error)),
    `browser request failures: ${JSON.stringify(failedRequests)}`,
  );
  for (const request of serverRequests) {
    assert.equal(request.method, "GET");
    assert.equal(request.status, 200);
    assert(allowedStaticPath(request.path), `unexpected static request ${request.path}`);
  }

  await writeFile(
    networkPath,
    `${JSON.stringify(
      {
        browserRequests,
        failedRequests,
        serverRequests,
      },
      null,
      2,
    )}\n`,
  );

  acceptance.browser = {
    arrow_only: isolationEvidence,
    base_url: origin,
    browser_version: browser.version(),
    console_errors: consoleErrors.length,
    integrity: integrityEvidence,
    interactions: interactionEvidence,
    network_log: relative(workdir, networkPath),
    network_requests: serverRequests.length,
    page_errors: pageErrors.length,
    publications_opened: PUBLICATIONS.map(([ownership, run]) => `${ownership}-${run}`),
    python_endpoint_requests: pythonEndpointRequests.length,
    rapid_transitions: rapidEvidence,
    relocation: {
      base: "relocated/deep/finance",
      rows: relocationEvidence.facts.rowCount,
    },
    screenshots,
    sparse_patch_assertions: stateEvidence.length,
    state_assertions: stateEvidence.length,
  };
  acceptance.cleanup.remaining_python_processes = pythonProcesses(workdir);
  acceptance.cleanup.remaining_server_sockets = 0;
  assert.equal(acceptance.cleanup.remaining_python_processes, 0);
  acceptance.completed_at = new Date().toISOString();
  acceptance.pass = true;
  acceptance.stage = "complete";
  delete acceptance.failure;
  await writeFile(acceptancePath, `${JSON.stringify(acceptance, null, 2)}\n`);
  process.stdout.write(
    `${JSON.stringify({
      acceptance: acceptancePath,
      browser_requests: serverRequests.length,
      screenshots: screenshots.length,
      states: stateEvidence.length,
    })}\n`,
  );
} catch (error) {
  acceptance.pass = false;
  acceptance.stage = "failed";
  acceptance.failure = {
    message: error instanceof Error ? error.message : String(error),
    type: error instanceof Error ? error.name : "Error",
  };
  await writeFile(acceptancePath, `${JSON.stringify(acceptance, null, 2)}\n`);
  throw error;
} finally {
  await browser?.close();
  await new Promise((resolvePromise, reject) => {
    server.close((error) => (error === undefined ? resolvePromise() : reject(error)));
  });
}

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || index + 1 >= process.argv.length) {
    throw new TypeError(`${name} is required`);
  }
  return process.argv[index + 1];
}

function createStaticServer(root, requests) {
  const server = createServer(async (request, response) => {
    let status = 500;
    let pathname = "/";
    try {
      assert.equal(request.method, "GET");
      const parsed = new URL(request.url ?? "/", "http://127.0.0.1");
      pathname = decodeURIComponent(parsed.pathname);
      const file = resolveStaticPath(
        root,
        pathname.endsWith("/") ? `${pathname}index.html` : pathname,
      );
      const payload = await readFile(file);
      status = 200;
      response.writeHead(status, {
        "Cache-Control": "no-store",
        "Content-Length": String(payload.byteLength),
        "Content-Type": contentType(file),
        "Cross-Origin-Resource-Policy": "same-origin",
      });
      response.end(payload);
    } catch (error) {
      status = error?.code === "ENOENT" ? 404 : 500;
      const message = status === 404 ? "Not found" : "Static server error";
      response.writeHead(status, {
        "Content-Length": String(Buffer.byteLength(message)),
        "Content-Type": "text/plain; charset=utf-8",
      });
      response.end(message);
    } finally {
      requests.push({
        method: request.method ?? "",
        path: pathname,
        status,
      });
    }
  });
  server.on("upgrade", (request, socket) => {
    requests.push({
      method: request.method ?? "",
      path: new URL(request.url ?? "/", "http://127.0.0.1").pathname,
      status: 426,
    });
    socket.destroy();
  });
  return server;
}

function resolveStaticPath(root, pathname) {
  const candidate = resolve(root, `.${pathname}`);
  assert(
    candidate === root || candidate.startsWith(`${root}${sep}`),
    "static request escaped the root",
  );
  return candidate;
}

function contentType(path) {
  return (
    {
      ".css": "text/css; charset=utf-8",
      ".html": "text/html; charset=utf-8",
      ".js": "text/javascript; charset=utf-8",
      ".json": "application/json; charset=utf-8",
      ".msgpack": "application/msgpack",
    }[extname(path)] ?? "application/octet-stream"
  );
}

function observePage(page, label, consoleErrors, pageErrors) {
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push({ label, text: message.text() });
    }
  });
  page.on("pageerror", (error) => {
    pageErrors.push({ label, message: error.message });
  });
}

async function choosePublication(page, ownership, run) {
  await page.evaluate(
    ({ ownershipValue, runValue }) => {
      const ownershipSelect = document.querySelector("#ownership-select");
      const runSelect = document.querySelector("#run-select");
      if (!(ownershipSelect instanceof HTMLSelectElement)) throw new Error("ownership missing");
      if (!(runSelect instanceof HTMLSelectElement)) throw new Error("run missing");
      ownershipSelect.value = ownershipValue;
      runSelect.value = runValue;
      ownershipSelect.dispatchEvent(new Event("change", { bubbles: true }));
    },
    { ownershipValue: ownership, runValue: run },
  );
  await waitReady(page, `${ownership}-${run}`, "baseline");
}

async function clickSparseState(page, state) {
  const button = page.locator(`#state-buttons button[data-state="${state}"]`);
  const patch = JSON.parse((await button.getAttribute("data-patch")) ?? "null");
  await button.click();
  const publication = await page.locator("#app").getAttribute("data-current-publication");
  assert.notEqual(publication, null);
  await waitReady(page, publication, state);
  return patch;
}

async function waitReady(page, publication, state) {
  await page.waitForFunction(
    ({ publicationKey, stateName }) => {
      const app = document.querySelector("#app");
      const diagnostics = window.__MARIMO_EXPORT_DEMO__;
      return (
        app?.getAttribute("data-status") === "ready" &&
        app.getAttribute("data-current-publication") === publicationKey &&
        app.getAttribute("data-current-state") === stateName &&
        diagnostics?.currentPublication === publicationKey &&
        diagnostics.currentState === stateName
      );
    },
    { publicationKey: publication, stateName: state },
    { timeout: 120_000 },
  );
}

async function inspectReadyState(page, publication, state) {
  await waitReady(page, publication, state);
  const evidence = await page.evaluate(() => {
    const diagnostics = window.__MARIMO_EXPORT_DEMO__;
    const image = document.querySelector("#chart-png img");
    return {
      diagnostics,
      imageCount: document.querySelectorAll("#chart-png img").length,
      metadataCount: document.querySelectorAll("#output-metadata [data-output]").length,
      png: {
        complete: image instanceof HTMLImageElement && image.complete,
        height: image instanceof HTMLImageElement ? image.naturalHeight : 0,
        width: image instanceof HTMLImageElement ? image.naturalWidth : 0,
      },
      stateSelect: document.querySelector("#state-select")?.value,
      vegaCount: document.querySelectorAll("#chart-vegalite .vega-embed").length,
      vegaSvgCount: document.querySelectorAll("#chart-vegalite svg").length,
      widgetCount: document.querySelectorAll("#dashboard .ohlc-widget").length,
    };
  });
  assert.equal(evidence.diagnostics.errors, 0);
  assert.equal(evidence.diagnostics.mounts - evidence.diagnostics.disposals, 3);
  assert.equal(evidence.stateSelect, state);
  assert.equal(evidence.metadataCount, 7);
  assert.equal(evidence.widgetCount, 1);
  assert.equal(evidence.vegaCount, 1);
  assert(evidence.vegaSvgCount >= 1);
  assert.equal(evidence.imageCount, 1);
  assert(evidence.png.complete && evidence.png.width > 0 && evidence.png.height > 0);
  assert.equal(evidence.diagnostics.facts.arrowRows, evidence.diagnostics.facts.rowCount);
  assert.equal(evidence.diagnostics.facts.parquetRows, evidence.diagnostics.facts.rowCount);
  assert.deepEqual(evidence.diagnostics.facts.numpyShape, [evidence.diagnostics.facts.rowCount, 4]);
  return {
    facts: evidence.diagnostics.facts,
    png: evidence.png,
    publication,
    state,
  };
}

async function exerciseLocalInteractions(page, serverRequests) {
  const before = serverRequests.length;
  const metric = page.locator("#dashboard .ohlc-controls button", { hasText: "Open" }).first();
  await metric.click();
  await page.waitForFunction(
    () =>
      document.querySelector('#dashboard .ohlc-controls button[data-active="true"]')
        ?.textContent === "Open",
  );
  assert.match(await page.locator("#dashboard .ohlc-subtitle").textContent(), /Open/u);

  const change = page.locator("#dashboard .ohlc-controls button", { hasText: "Change" }).first();
  await change.click();
  await page.waitForFunction(() =>
    document.querySelector("#dashboard .ohlc-subtitle")?.textContent?.includes("relative move"),
  );

  const symbol = page.locator("#dashboard .ohlc-symbols button", { hasText: "AMZN" }).first();
  const prior = await symbol.getAttribute("data-active");
  await symbol.click();
  await page.waitForFunction(
    ({ priorValue }) =>
      document
        .querySelector("#dashboard .ohlc-symbols button:nth-last-child(1)")
        ?.getAttribute("data-active") !== priorValue,
    { priorValue: prior },
  );

  const beforePulses = await page.locator("#vega-signal").getAttribute("data-pulses");
  await page.locator("#vega-signal").click();
  await page.waitForFunction(
    ({ prior }) => {
      const current = document.querySelector("#vega-signal")?.getAttribute("data-pulses");
      return Number(current ?? "0") > Number(prior ?? "0");
    },
    { prior: beforePulses },
  );
  const vegaPulses = Number(await page.locator("#vega-signal").getAttribute("data-pulses"));
  await page.waitForTimeout(100);
  assert.equal(serverRequests.length, before, "local widget interaction triggered a request");
  return {
    metric: "Open",
    mode: "change",
    symbol: "AMZN",
    vegaPulses,
  };
}

async function captureSurfaces(page, directory, screenshots) {
  const viewports = [
    { label: "desktop", viewport: { width: 1440, height: 1000 } },
    { label: "narrow", viewport: { width: 430, height: 900 } },
  ];
  for (const { label, viewport } of viewports) {
    await page.setViewportSize(viewport);
    for (const state of ["baseline", "focus", "compact"]) {
      await clickSparseState(page, state);
      const path = resolve(directory, `${label}-${state}.png`);
      await page.screenshot({ fullPage: true, path });
      screenshots.push(relative(workdir, path));
    }
    await page.locator("#unavailable-state").click();
    await page.locator('#unavailable-panel[data-error-code="state_unavailable"]').waitFor({
      state: "visible",
    });
    const path = resolve(directory, `${label}-unavailable.png`);
    await page.screenshot({ fullPage: true, path });
    screenshots.push(relative(workdir, path));
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
}

async function exerciseRapidTransitions(page) {
  const before = await page.evaluate(() => ({
    disposals: window.__MARIMO_EXPORT_DEMO__.disposals,
    mounts: window.__MARIMO_EXPORT_DEMO__.mounts,
    transitions: window.__MARIMO_EXPORT_DEMO__.transitions,
  }));
  await page.evaluate(
    (states) => {
      for (const state of states) {
        document.querySelector(`#state-buttons button[data-state="${state}"]`)?.click();
      }
    },
    ["focus", "compact", "narrow_universe", "short_window", "weekly"],
  );
  await waitReady(page, "capture-cold", "weekly");
  const after = await page.evaluate(() => ({
    currentState: window.__MARIMO_EXPORT_DEMO__.currentState,
    disposals: window.__MARIMO_EXPORT_DEMO__.disposals,
    errors: window.__MARIMO_EXPORT_DEMO__.errors,
    imageCount: document.querySelectorAll("#chart-png img").length,
    mounts: window.__MARIMO_EXPORT_DEMO__.mounts,
    transitions: window.__MARIMO_EXPORT_DEMO__.transitions,
    vegaCount: document.querySelectorAll("#chart-vegalite .vega-embed").length,
    widgetCount: document.querySelectorAll("#dashboard .ohlc-widget").length,
  }));
  assert.equal(after.currentState, "weekly");
  assert.equal(after.errors, 0);
  assert.equal(after.mounts - after.disposals, 3);
  assert.equal(after.widgetCount, 1);
  assert.equal(after.vegaCount, 1);
  assert.equal(after.imageCount, 1);
  assert(after.transitions >= before.transitions + 5);
  return { after, before };
}

function pythonProcesses(root) {
  return execFileSync("ps", ["-axo", "pid=,command="], { encoding: "utf8" })
    .split("\n")
    .filter((line) => {
      const fields = line.trim().split(/\s+/u);
      if (fields.length < 2 || Number(fields[0]) === process.pid || !line.includes(root)) {
        return false;
      }
      const program = fields[1].split("/").at(-1)?.toLowerCase() ?? "";
      return program.startsWith("python");
    }).length;
}

function allowedStaticPath(path) {
  return (
    path === "/" ||
    path === "/index.html" ||
    path === "/arrow-only.html" ||
    /^\/assets\/[^/]+$/u.test(path) ||
    /^\/publications\/(?:capture|build)-(?:cold|warm)\/(?:index\.json|assets\/[a-f0-9]{64}\.[a-z0-9]+)$/u.test(
      path,
    ) ||
    /^\/relocated\/deep\/finance\/(?:index\.json|assets\/[a-f0-9]{64}\.[a-z0-9]+)$/u.test(path) ||
    /^\/tampered\/(?:index\.json|assets\/[a-f0-9]{64}\.[a-z0-9]+)$/u.test(path)
  );
}
