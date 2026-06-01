import assert from "node:assert/strict";
import test from "node:test";

import { captureExportWithClient } from "../dist/index.js";

test("captureExportWithClient resolves with the completed bundle result", async () => {
  const calls = [];
  const payload = {
    bundle_path: "/tmp/export/bundles/sha256-demo",
    manifest_path: "/tmp/export/bundles/sha256-demo/manifest.json",
    invocation_path: "/tmp/export/bundles/sha256-demo/traces/invocation.json",
    invocation_index_path: "/tmp/export/bundles/sha256-demo/traces/index.json",
    manifest: { id: "sha256-demo" },
    invocation: { id: "invocation-demo" },
  };
  const client = {
    async POST(path) {
      calls.push(["POST", path]);
      throw new Error("captureExportWithClient should not dispatch async scratchpad runs");
    },
    async executeScratchpad({ code, sessionId }) {
      calls.push(["executeScratchpad", sessionId]);
      const candidates = [...code.matchAll(/"((?:\\.|[^"\\])*)"/g)].map((match) =>
        JSON.parse(`"${match[1]}"`),
      );
      assert.ok(candidates.length, "scratchpad code should include string literals");
      return {
        success: true,
        output: null,
        stdout: candidates.map((candidate) => `${candidate}${JSON.stringify(payload)}\n`),
        stderr: [],
      };
    },
    async openNotebook() {
      throw new Error("sessionId should avoid notebook opening");
    },
  };

  const result = await captureExportWithClient(
    {
      scenarios: [{ id: "default" }],
      values: {},
    },
    {
      client,
      sessionId: "session-1",
      runtime: false,
      bundle: "/tmp/export",
    },
  );

  assert.deepEqual(calls, [["executeScratchpad", "session-1"]]);
  assert.equal(result.bundlePath, payload.bundle_path);
  assert.equal(result.manifestPath, payload.manifest_path);
  assert.deepEqual(result.manifest, payload.manifest);
  assert.deepEqual(result.invocation, payload.invocation);
});
