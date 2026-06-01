import assert from "node:assert/strict";
import test from "node:test";

import { captureExport } from "../dist/index.js";

test("captureExport delegates to an explicit export client", async () => {
  const calls = [];
  const payload = {
    bundlePath: "/tmp/export/bundles/sha256-demo",
    manifestPath: "/tmp/export/bundles/sha256-demo/manifest.json",
    invocationPath: "/tmp/export/bundles/sha256-demo/traces/invocation.json",
    invocationIndexPath: "/tmp/export/bundles/sha256-demo/traces/index.json",
    manifest: { id: "sha256-demo" },
    invocation: { id: "invocation-demo" },
  };
  const client = {
    marimo: {},
    async capture(spec, options) {
      calls.push(["capture", spec, options]);
      return {
        session: { sessionId: "session-1", name: null, path: null, initializationId: null },
        ...payload,
      };
    },
    async captureArchive() {
      throw new Error("captureExport should not request an archive");
    },
    async listSessions() {
      throw new Error("captureExport should not list sessions");
    },
    async listWorkspaceNotebooks() {
      throw new Error("captureExport should not list workspace notebooks");
    },
  };
  const spec = {
    scenarios: [{ id: "default" }],
    values: {
      title: {
        source: { def: "title" },
        artifacts: ["text"],
      },
    },
  };

  const result = await captureExport(spec, { client, sessionId: "session-1", to: "/tmp/export" });

  assert.deepEqual(calls, [["capture", spec, { to: "/tmp/export", sessionId: "session-1" }]]);
  assert.equal(result.bundlePath, payload.bundlePath);
  assert.equal(result.manifestPath, payload.manifestPath);
  assert.deepEqual(result.manifest, payload.manifest);
  assert.deepEqual(result.invocation, payload.invocation);
});
