---
title: Verification and trust
description: Learn what verification checks, how to trust a publisher, and when an output can run browser code.
---

# Verification and trust

The quickstart's `report` output points to an asset file named by its SHA-256
digest. Its descriptor records the expected path, size, and digest.

If one byte changes, `marimo-export verify` fails. If another publisher replaces
`index.json` and every asset with a different self-consistent export,
verification can succeed and the export identity changes. The first case is an
integrity failure. The second requires publisher authentication.

## Verification starts with `index.json`

`index.json` uses canonical JSON, so one supported value has one byte
representation. The SHA-256 digest of those exact bytes is the export identity.

Each asset path follows from its codec and digest. The exporter, the Python
reader, and `marimo-export verify` reject an asset when its path, size, digest,
framing, or descriptor agreement is invalid. The browser reader applies the same
checks except for the digest comparison.

## Opening, loading, and verifying read different files

| Operation                                   | Reads                     | Establishes                                                      |
| ------------------------------------------- | ------------------------- | ---------------------------------------------------------------- |
| `open_export()` or `openExport()`           | `index.json`              | Canonical index and state-output relation                        |
| Python asset-backed output read             | One selected output asset | Descriptor, size, format, and digest agreement                   |
| Browser `load()`                            | One selected output asset | Size, NPY, Arrow, or BlobAsset framing, then the loader's parser |
| `verify_export()` or `marimo-export verify` | Every declared asset      | Complete declared asset closure, with digests                    |
| Browser `NotebookExport.verify()`           | Every declared asset      | Size of every asset, and NPY, Arrow, or BlobAsset framing        |

Opening leaves assets unread. Run `marimo-export verify` before deployment,
before an agent treats exported data as evidence, or while investigating storage
corruption.

## Browsers rely on digests checked at build time

The exporter computes every digest and verifies the export before it commits the
directory. A browser reader requests each asset by its digest-named path and
checks its canonical index, byte length, and NPY, Arrow, or BlobAsset framing. It
runs on any HTTP or HTTPS origin, including a plain-HTTP address on a private
network where
[`crypto.subtle`](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/subtle)
is undefined.

A same-sized change made in transit, in storage, or in a CDN or HTTP cache reaches
the loader undetected. Serve exports over HTTPS from a host you trust as much as
the application page, because a mounted AnyWidget or chart from that host runs
with the page's authority.

## Integrity does not identify the publisher

`marimo-export verify` establishes consistency with the loaded `index.json`.
Authenticate who supplied that index through a controlled origin, authenticated transport,
trusted artifact registry, or separate signature policy.

In Python, keep the export identity with a result when a consumer must pin exact content.
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
