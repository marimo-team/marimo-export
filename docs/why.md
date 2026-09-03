---
title: Why export notebook states?
description: Decide when finite notebook precomputation fits an application, agent, or deployment.
---

# Why export notebook states?

A [marimo](https://marimo.io/) notebook can read private data, import Python
packages, and compute interactive results. A deployed application must either
run that Python computation for each request or use results prepared earlier.

marimo-export moves a finite set of results across that boundary. The producer
runs selected notebook states in Python. It writes one notebook export that
consumers can read after the producer stops.

## A concrete case

Consider a report with an `interval` control and two published outputs:

| State alias | `interval` | Published outputs  |
| ----------- | ---------- | ------------------ |
| `daily`     | `1d`       | `summary`, `chart` |
| `weekly`    | `1wk`      | `summary`, `chart` |

The producer evaluates both rows through marimo's dependency graph, which orders
cells from the definitions they use. A static browser application can then
switch between `daily` and `weekly`, load the matching summary and chart, and
render them without contacting Python.

```text
producer environment                 consumer environment

notebook code                        browser application
private files          index.json    Python automation
Python packages      + assets  --->  agent
credentials                          custom reader
expensive computation
```

The notebook remains the source of the computation. The notebook export becomes
the source of the selected results.

## What becomes portable

Each notebook export contains:

- a finite set of complete input vectors
- the same named outputs for every state
- a declared representation for each output
- notebook, producer, state, and output provenance
- content identities for the index and assets

Consumers can select and inspect those records without importing the notebook's
Python environment. Browser consumers fetch `index.json` and assets over HTTP.
Python consumers open the same directory from the filesystem.

## What remains in the producer

The producer keeps responsibility for:

- executing notebook code
- reading source data and credentials
- importing Python dependencies
- applying state inputs through marimo
- converting notebook results into portable representations
- preparing another export when the requested state changes

A notebook export contains results for its declared states. An application that
accepts arbitrary new inputs needs a Python producer or another service that can
compute and publish those results.

## When the model fits

Use marimo-export when the application can name the states it intends to serve.
Common fits include:

- a report with daily, weekly, and monthly views
- a dashboard with a bounded set of regions or cohorts
- an agent that needs verified structured results and source identity
- a static site that publishes notebook charts and tables
- an application that refreshes by replacing one immutable export with another

The number and size of states affect producer work and export size. Author the
smallest state set that supports the consumer's decisions.

## Choose the execution boundary

| Application need                                                     | Execution model                                                  |
| -------------------------------------------------------------------- | ---------------------------------------------------------------- |
| Resolve a declared finite set of states after the producer stops     | Prepare a notebook export with marimo-export                     |
| Evaluate arbitrary inputs against current private data               | Keep a Python service in the request path                        |
| Run a compatible notebook and its Python dependencies in the browser | Use marimo's [WebAssembly](https://webassembly.org/) export path |

WebAssembly is a portable browser instruction format. marimo uses a browser
Python runtime to execute compatible notebooks on the client. A notebook export
uses a different boundary: the browser selects prepared results and runs no
notebook Python.

Use a Python service when a request must:

- evaluate an input vector absent from the notebook export
- mutate notebook state and return a new computation immediately
- access current private data for every request
- call a Python function that cannot be represented as exported data

The service can still publish notebook exports for repeatable or cacheable
parts of the application.

## One contract for several consumers

Python readers, browser readers, agents, and custom clients consume the same
state-output relation. They differ in how they decode an output representation.

This separation lets the producer choose Python tools while each consumer loads
only the representations it understands. Read [What is
marimo-export?](overview) for the complete lifecycle or start with [States
and inputs](concepts/states-and-inputs).
