# Development documentation

These documents define how to change and validate marimo-export.

- [`architecture.md`](./architecture.md) defines the live kernel, cache, transfer, publication, Python reader, browser reader, and loader boundaries.
- [`development.md`](./development.md) covers workspace setup, package ownership, schema changes, format extensions, and private marimo integration.
- [`validation.md`](./validation.md) maps each change surface to unit, integration, package, and native browser evidence, including the Chromium AnyWidget gate.

User installation, specification authoring, capture, and reading belong in [`docs`](../docs). Read [`architecture.md`](./architecture.md) before changing private marimo imports, projection identity, transfer tickets, publication schemas, or package exports.
