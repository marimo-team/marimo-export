import { resolve } from "node:path";

import { openExport } from "../packages/client/dist/index.mjs";
import { directorySource } from "../packages/client/dist/node.mjs";

const root = resolve(process.argv[2] ?? "/tmp/cache-matrix-export");
const published = await openExport(directorySource(root));
const scenarios = await Promise.all(
  published.scenarios().map(async (scenario) => ({
    id: scenario.id,
    inputs: scenario.inputs,
    projected: await scenario.output("projected", "json").json(),
  })),
);

console.log(JSON.stringify({ scenarios }, null, 2));
