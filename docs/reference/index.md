---
title: Reference
description: Exact contracts for StateSpace, ExportSpec, notebook exports, commands, Python, browser APIs, representations, portable JSON, and project terminology.
---

# Reference

Reference pages define exact public contracts. Start with the
[quickstart](../guide/getting-started) for a complete workflow or [What is
marimo-export?](../overview) for the main concepts.

## Author and inspect exports

| Need                                                 | Reference                                 |
| ---------------------------------------------------- | ----------------------------------------- |
| Declare a state space and output sources             | [StateSpace and ExportSpec](export-spec)  |
| Match a notebook value to a stored form and consumer | [Output representations](representations) |
| Implement or inspect the portable directory          | [Export format](export-format)            |
| Run commands or consume human and machine output     | [CLI](cli)                                |

## Use Python

| Need                                                                    | Reference                                                         |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Choose the Python surface                                               | [Python API](python-api)                                          |
| Plan, prepare, capture, build, and write                                | [Produce an export](python/produce)                               |
| Open, select, decode, and verify                                        | [Python reader](python/reader)                                    |
| Connect to sessions and inspect notebooks                               | [Sessions and inspection](python/sessions-and-inspection)         |
| Retain prepared work and observations                                   | [Repository and observations](python/repository-and-observations) |
| Retain a Python prepared publication or commit an application directory | [Delivery and publications](python/delivery-and-publications)     |
| Integrate an interactive marimo host                                    | [Host integration](python/host-integration)                       |
| Encode canonical values or handle typed failures                        | [Format records and errors](python/format-records-and-errors)     |

## Use a browser

| Need                                                | Reference                                              |
| --------------------------------------------------- | ------------------------------------------------------ |
| Choose the browser surface                          | [Browser API](browser-api)                             |
| Open, select, load, and verify                      | [Browser reader](browser/reader)                       |
| Follow a prepared manifest and commit browser state | [Prepared publications](browser/prepared-publications) |
| Decode or mount one representation                  | [Output loaders](browser/loaders)                      |
| Consume rendered-output and complete-cell records   | [marimo snapshots](browser/snapshots)                  |
| Handle failures and configure byte limits           | [Browser errors and limits](browser/errors-and-limits) |

## Cross-language contracts

- [Portable JSON](portable-json) defines the shared Python and JavaScript value
  boundary and identifies the published interfaces in each language.
- [Compatibility](compatibility) defines Python, marimo, browser, package,
  and protocol boundaries.
- [Terminology](terminology) provides a short lookup map for project nouns.
