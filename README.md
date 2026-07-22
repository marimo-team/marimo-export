# marimo-export

Publish selected marimo notebook results as verified static data for frontend apps and agents to read without Python.

The notebook remains the source of truth. An export plan declares a finite set of input scenarios and the projections to publish from each one. `marimo-export` runs that plan in an existing marimo server, reuses marimo's native cell cache, and pulls the resulting index and content-addressed payloads to the consumer.

Use the remote machine for Python, data, and accelerator work. Use the published files from browsers, Node, Next.js, Astro, static sites, or CLI-driven agents after the producer stops.

## Install

Install the Python producer in the notebook environment:

```bash
uv add marimo-export
```

Install the client and reader in the consuming project:

```bash
pnpm add @marimo-team/marimo-export
```

## Publish

Connect to a running `marimo edit` server and publish a notebook plan:

```bash
marimo-export publish \
  --server http://127.0.0.1:2718/ \
  --notebook /absolute/path/on/server/notebook.py \
  --plan notebook.plan.yaml \
  --out public/export
```

The notebook path is resolved on the server. Set `MARIMO_TOKEN` or `MARIMO_SERVER_TOKEN` when the server requires authentication. `marimo-export` uses the environment and kernel managed by that server.

An export plan names notebook inputs, scenario values, output sources, and portable formats. Built-in formats include JSON, text, HTML, bytes, Arrow, Parquet, Vega-Lite, PNG, and AnyWidget. A notebook can return a custom `Projection` when an application needs a different payload contract.

Follow [Getting started](docs/getting-started.md) for a complete local workflow. See [Export plans](docs/export-plans.md) for the plan schema and projection formats.

## Read

Serve the publication at `/export/`, then read it from a browser or server runtime:

```ts
import { httpSource, openExport } from "@marimo-team/marimo-export";

const published = await openExport(httpSource("/export/"));
const scenario = published.scenario("baseline");
const summary = await scenario.output("summary", "json").json();
```

For a local directory in Node, import `directorySource` from `@marimo-team/marimo-export/node`. Every output is checked against the byte size and SHA-256 digest recorded in the publication index before it is decoded.

Agents can inspect and read the same publication through bounded JSON commands:

```bash
marimo-export inspect public/export --json
marimo-export read public/export baseline summary --format json --json
```

See [Read exports](docs/read-exports.md) for browser, Node, Next.js, and Astro examples. See [CLI](docs/cli.md) for command output and exit codes.

## More

- [Remote execution](docs/remote-execution.md)
- [AnyWidget](docs/anywidget.md)
- [Trust and integrity](docs/trust.md)
- [Contributor documentation](development_docs/README.md)

Licensed under the [Apache License 2.0](LICENSE).
