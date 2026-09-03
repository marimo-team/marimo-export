---
title: Reference
description: Exact contracts for StateSpace, ExportSpec, notebook exports, commands, Python, browser APIs, representations, portable JSON, and project terminology.
---

# Reference

Reference pages define names, signatures, accepted shapes, defaults, return
values, errors, side effects, and lifecycle ownership. Start with the
[quickstart](../guide/getting-started.md) for a complete workflow or [What is a
notebook export?](../overview.md) for the product model.

## Author and inspect exports

| Need                                                 | Reference                                    |
| ---------------------------------------------------- | -------------------------------------------- |
| Declare a state space and output sources             | [StateSpace and ExportSpec](export-spec.md)  |
| Match a notebook value to a stored form and consumer | [Output representations](representations.md) |
| Implement or inspect the portable directory          | [Export format](export-format.md)            |
| Run commands and consume machine output              | [CLI](cli.md)                                |

## Use Python

| Need                                                  | Reference                                                            |
| ----------------------------------------------------- | -------------------------------------------------------------------- |
| Choose the Python surface                             | [Python API](python-api.md)                                          |
| Plan, prepare, capture, build, and write              | [Produce an export](python/produce.md)                               |
| Open, select, decode, and verify                      | [Python reader](python/reader.md)                                    |
| Connect to sessions and inspect notebooks             | [Sessions and inspection](python/sessions-and-inspection.md)         |
| Retain prepared work and observations                 | [Repository and observations](python/repository-and-observations.md) |
| Serve publications and commit application directories | [Delivery and publications](python/delivery-and-publications.md)     |
| Integrate an interactive marimo host                  | [Host integration](python/host-integration.md)                       |
| Construct protocol records and handle failures        | [Format records and errors](python/format-records-and-errors.md)     |

## Use a browser

| Need                                               | Reference                                                 |
| -------------------------------------------------- | --------------------------------------------------------- |
| Choose the browser surface                         | [Browser API](browser-api.md)                             |
| Open, select, load, and verify                     | [Browser reader](browser/reader.md)                       |
| Follow a mutable manifest and commit state changes | [Prepared publications](browser/prepared-publications.md) |
| Decode or mount one representation                 | [Output loaders](browser/loaders.md)                      |
| Consume rendered-output and complete-cell records  | [marimo snapshots](browser/snapshots.md)                  |
| Handle failures and configure byte limits          | [Browser errors and limits](browser/errors-and-limits.md) |

## Cross-language contracts

- [Portable JSON](portable-json.md) defines the shared Python and JavaScript
  value boundary and optional Zod schemas.
- [Terminology](terminology.md) maps each project noun to one precise contract.
