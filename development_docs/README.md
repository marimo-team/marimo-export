# Development documentation

These documents define how to change and validate marimo-export.

- [`architecture.md`](./architecture.md) defines the producer, transfer, consumer, and codec planes. It also specifies cache identity, wire formats, lifecycle boundaries, and upstream integration seams.
- [`development.md`](./development.md) covers workspace setup, package ownership, custom formats, upstream upgrades, and focused development commands.
- [`validation.md`](./validation.md) maps each change surface to the checks and runtime evidence required before handoff.

User installation, plan authoring, publishing, and consumption belong in [`docs`](../docs). Start with [`architecture.md`](./architecture.md) before changing private marimo integration, projection identity, remote staging, schemas, or package exports.
