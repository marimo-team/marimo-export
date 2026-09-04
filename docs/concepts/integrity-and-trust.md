---
title: Verification and trust
description: Learn what verification checks, how to trust a publisher, and when an output can run browser code.
---

# Verification and trust

The quickstart's `report` output points to an asset file named by its SHA-256
digest. Its descriptor records the expected path, size, and digest.

If one byte changes, verification fails. If another publisher replaces
`index.json` and every asset with a different self-consistent export,
verification can succeed and the export identity changes. The first case is an
integrity failure. The second requires publisher authentication.

## Verification starts with `index.json`

`index.json` uses canonical JSON, so one supported value has one byte
representation. The SHA-256 digest of those exact bytes is the export identity.

Each asset path follows from its codec and digest. A reader rejects an asset when
its path, size, digest, framing, or descriptor agreement is invalid.

## Opening, loading, and verifying read different files

| Operation                                      | Reads                     | Establishes                                    |
| ---------------------------------------------- | ------------------------- | ---------------------------------------------- |
| `open_export()` or `openExport()`              | `index.json`              | Canonical index and state-output relation      |
| Asset-backed output read or browser `load()`   | One selected output asset | Descriptor, size, format, and digest agreement |
| `verify_export()` or `NotebookExport.verify()` | Every declared asset      | Complete declared asset closure                |

Opening leaves assets unread. Run complete verification before deployment,
before an agent treats exported data as evidence, or while investigating storage
corruption.

## Integrity does not identify the publisher

Verification establishes consistency with the loaded `index.json`. Authenticate
who supplied that index through a controlled origin, authenticated transport,
trusted artifact registry, or separate signature policy.

Keep the export identity with a result when a consumer must pin exact content.
State fingerprints, output names, representations, and asset digests can further
identify the selected evidence.

## Producer operations execute notebook code

`build`, `prepare`, and file inspection run notebook code with the producer's
file, credential, network, and package access. `capture` runs selected states
with the authority of the chosen live session.

Review the notebook, imported code, custom exporters, and selected session before
starting a producer operation.

## Mounted outputs can run code on the page

Opening, resolving, loading inert data, and verifying do not import
notebook-authored browser modules. Mounting AnyWidget, Vega-Lite, or custom
interactive output can execute JavaScript and request external resources with
the page's authority.

Before mounting executable output:

- review the module or chart specification
- configure [Content Security Policy
  (CSP)](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CSP) and allowed
  origins
- set byte limits for untrusted exports
- cancel stale loads and dispose replaced mounts

An HTML loader or rendered-output snapshot returns inert markup data. Sanitize
and render that markup under the application's HTML policy.

## Cache entries are signed

[marimo](https://marimo.io/) owns signing and verification for computation-cache restoration.
Notebook export verification protects the portable files after preparation.
Configure both when the producer restores cached computation and consumers must
verify exported bytes.

[Build and capture](../guide/build-and-capture) produces an export. [Read an
export](../guide/consume-an-export) covers Python, browser, and agent consumers.
Use the [export format reference](../reference/export-format) for exact canonical
JSON, codec, asset, and size rules.
