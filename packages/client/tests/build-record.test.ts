import { describe, expect, test } from "vite-plus/test";

import {
  createRemoteBuildRecord,
  parseRemoteBuildRecord,
  REMOTE_BUILD_RECORD_SCHEMA,
} from "../src/node/build-record.js";

const ref = {
  key: `marimo-export/indexes/${"a".repeat(64)}.json` as const,
  sha256: "a".repeat(64),
  size: 42,
};

const receipt = { elapsedMs: 4.5, scenarioCount: 2, projectionCount: 6 };

describe("build record", () => {
  test("creates the durable record from an explicit notebook locator and runtime result", () => {
    expect(
      createRemoteBuildRecord({
        server: "https://marimo.test/",
        notebook: "examples/_notebooks/finance.py",
        build: { ref, receipt },
      }),
    ).toEqual({
      schema: REMOTE_BUILD_RECORD_SCHEMA,
      server: "https://marimo.test/",
      target: { notebook: "examples/_notebooks/finance.py" },
      ref,
      receipt,
    });
  });

  test("parses the strict credential-free remote pointer", () => {
    expect(
      parseRemoteBuildRecord({
        schema: REMOTE_BUILD_RECORD_SCHEMA,
        server: "https://marimo.test/",
        target: { notebook: "examples/_notebooks/finance.py" },
        ref,
        receipt,
      }),
    ).toEqual({
      schema: REMOTE_BUILD_RECORD_SCHEMA,
      server: "https://marimo.test/",
      target: { notebook: "examples/_notebooks/finance.py" },
      ref,
      receipt,
    });
  });

  test("rejects stale receipt fields and embedded credentials", () => {
    expect(() =>
      parseRemoteBuildRecord({
        schema: REMOTE_BUILD_RECORD_SCHEMA,
        server: "https://marimo.test/",
        target: { notebook: "examples/_notebooks/finance.py" },
        ref,
        receipt: {
          elapsedMs: 1,
          scenarioCount: 1,
          projectionCount: 1,
          projection_hits: 1,
        },
      }),
    ).toThrow("unexpected fields: projection_hits");
    expect(() =>
      parseRemoteBuildRecord({
        schema: REMOTE_BUILD_RECORD_SCHEMA,
        server: "https://token@marimo.test/",
        target: { notebook: "examples/_notebooks/finance.py" },
        ref,
        receipt: { elapsedMs: 1, scenarioCount: 1, projectionCount: 1 },
      }),
    ).toThrow("must not contain credentials");
  });

  test("requires a notebook target that pull can reopen", () => {
    expect(() =>
      parseRemoteBuildRecord({
        schema: REMOTE_BUILD_RECORD_SCHEMA,
        server: "https://marimo.test/",
        target: { sessionId: "s_running" },
        ref,
        receipt: { elapsedMs: 1, scenarioCount: 1, projectionCount: 1 },
      }),
    ).toThrow("must contain exactly notebook so pull can open a fresh session");
  });
});
