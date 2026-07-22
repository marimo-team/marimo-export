#!/usr/bin/env node

import { runCli } from "./node/cli.js";

const controller = new AbortController();
const cancel = () => controller.abort(new DOMException("Cancelled.", "AbortError"));
process.once("SIGINT", cancel);
process.once("SIGTERM", cancel);
try {
  const result = await runCli(process.argv.slice(2), undefined, controller.signal);
  process.exitCode = result.exitCode;
} finally {
  process.removeListener("SIGINT", cancel);
  process.removeListener("SIGTERM", cancel);
}
