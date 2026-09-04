import assert from "node:assert/strict";
import test from "node:test";

import {
  type ExamplePublicationFileSystem,
  type ExamplePublicationPaths,
  publishExample,
} from "./example-publication.ts";

const paths: ExamplePublicationPaths = {
  destination: "/docs/public/examples/market-dashboard",
  previous: "/docs/cache/market-dashboard-previous",
  staging: "/docs/cache/market-dashboard-staging",
};

const fixture = (existing = true) => {
  const entries = new Set(existing ? [paths.destination, paths.staging] : [paths.staging]);
  const operations: ExamplePublicationFileSystem = {
    async remove(path) {
      entries.delete(path);
    },
    async rename(source, destination) {
      if (!entries.delete(source)) {
        const error = new Error(`Missing ${source}`);
        Object.assign(error, { code: "ENOENT" });
        throw error;
      }
      entries.add(destination);
    },
  };
  return { entries, operations };
};

void test("publishes a staged example over the current tree", async () => {
  const { entries, operations } = fixture();
  await publishExample(paths, operations);
  assert.deepEqual([...entries], [paths.destination]);
});

void test("publishes the first staged example", async () => {
  const { entries, operations } = fixture(false);
  await publishExample(paths, operations);
  assert.deepEqual([...entries], [paths.destination]);
});

void test("restores the current tree when staged publication fails", async () => {
  const { entries, operations } = fixture();
  const failingOperations: ExamplePublicationFileSystem = {
    ...operations,
    async rename(source, destination) {
      if (source === paths.staging) throw new Error("publish failed");
      await operations.rename(source, destination);
    },
  };
  await assert.rejects(publishExample(paths, failingOperations), /publish failed/);
  assert.deepEqual([...entries], [paths.destination]);
});
