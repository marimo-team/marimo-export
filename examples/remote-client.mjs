import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { pullRemote } from "../packages/client/dist/node.mjs";
import { connectRemote } from "../packages/client/dist/remote.mjs";

const server = new URL(process.env.MARIMO_EXPORT_SERVER ?? "http://127.0.0.1:2718/");
const notebook = process.env.MARIMO_EXPORT_NOTEBOOK ?? "examples/_notebooks/cache_matrix.py";
const planPath = resolve(
  process.env.MARIMO_EXPORT_PLAN ?? "examples/_notebooks/cache_matrix.plan.json",
);
const into = resolve(process.env.MARIMO_EXPORT_OUT ?? "/tmp/cache-matrix-export");
const plan = JSON.parse(await readFile(planPath, "utf8"));
const authToken = process.env.MARIMO_TOKEN;
const serverToken = process.env.MARIMO_SERVER_TOKEN;

const remote = await connectRemote({
  server,
  target: { notebook },
  timeoutMs: 300_000,
  ...(authToken === undefined || authToken.length === 0 ? {} : { authToken }),
  ...(serverToken === undefined || serverToken.length === 0 ? {} : { serverToken }),
});

try {
  const description = await remote.describe();
  const result = await remote.build(plan);
  const pull = await pullRemote(remote, result.ref, { into });
  console.log(JSON.stringify({ description, build: result, pull, into }, null, 2));
} finally {
  await remote.close();
}
