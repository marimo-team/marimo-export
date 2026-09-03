---
title: Integrity and trust
description: Distinguish validation, content integrity, provenance, publisher authentication, cache signing, and executable browser authority.
---

# Integrity and trust

Suppose a notebook export declares a chart asset with a size and SHA-256 digest.
If one byte in that asset changes, `verify` reports an integrity failure. If a
different producer replaces both `index.json` and every declared asset with a
self-consistent export, structural verification can succeed and the export
identity changes.

These cases separate integrity from publisher trust.

## index.json is the integrity root

A notebook export starts at canonical `index.json`. Canonical JSON has one byte
representation for the same supported value. Readers reject an index whose key
order, string encoding, number spelling, tags, or fields differ from the
declared format.

The notebook export identity is the lowercase
[SHA-256](https://csrc.nist.gov/pubs/fips/180-4/upd1/final) digest of the exact
canonical `index.json` bytes. A changed index has a changed identity.

Each asset declaration contains:

- a codec that determines its content-addressed suffix
- the expected byte size
- the expected SHA-256 digest

The asset path follows from the codec and digest. Readers reject a path,
declared size, observed size, required NumPy or Arrow framing, or digest
that disagrees with the descriptor.

## Opening, loading, and verifying check different scopes

| Operation                                      | Reads                                          | Establishes                                                                                   |
| ---------------------------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `open_export()` or `openExport()`              | `index.json`                                   | Canonical index shape, state fingerprints, representation consistency, and asset declarations |
| Output read or `load()`                        | One selected output and its asset when present | Descriptor agreement, byte limits, framing, size, and SHA-256 before decoding                 |
| `verify_export()` or `NotebookExport.verify()` | Every declared asset                           | Complete declared asset closure and verified counts                                           |

Opening leaves assets lazy. Use complete verification before deployment, before
an agent treats exported data as evidence, or when storage corruption is under
investigation.

## Content identities answer different questions

| Identity                        | Question                                                      |
| ------------------------------- | ------------------------------------------------------------- |
| `NotebookExport.identity`       | Are these the exact canonical index bytes I selected?         |
| `spec_sha256`                   | Which exact `ExportSpec` selected the state-output relation?  |
| State fingerprint               | Which complete input vector produced this state's outputs?    |
| Asset SHA-256                   | Are these the exact bytes declared for this output asset?     |
| Producer implementation SHA-256 | Which marimo-export Python implementation created the export? |

Keep the relevant identities with a data-driven claim. They let another consumer
check that it selected the same notebook export, state, output, and bytes.

## Verification and publisher authentication are separate

`verify` establishes consistency with the loaded `index.json`. Publisher
authentication identifies the person or system that supplied the index.

Establish publisher trust through a controlled deployment, an authenticated
transport, a trusted artifact registry, or a separate signature policy. Record
the expected notebook export identity when a consumer must pin exact content.

Provenance records support inspection. They include notebook and producer facts,
state identities, output codec and media type, stored Python type, and asset
identity. Exporter-backed outputs record `marimo_export.outputs.BlobAsset` as
the stored Python type. Provenance remains data from the producer until a
trusted channel authenticates that producer.

## marimo cache signing protects another boundary

marimo owns computation-cache persistence and signing. Its signing policy and
trusted keys determine whether the producer accepts a restored cache entry.

Notebook export verification protects the portable directory after
preparation. Cache signing and export verification cover different files at
different lifecycle stages. Configure both when the producer restores cached
computation and consumers require verified export bytes.

## Producer operations run notebook code

`build`, `prepare`, and file-based notebook inspection run notebook code with
the producer environment's file, credential, network, and package access.
`capture` runs selected states in the chosen live session with that session's
authority.

Review the notebook, its imported code, custom exporters, and selected session
before starting a producer operation. Pin external data and dependencies when a
replacement export must reproduce the same result.

## Browser mounting grants page authority

Opening, resolving, loading inert data, and verifying do not execute
notebook-authored browser modules. Mounting AnyWidget, Vega-Lite, or a custom
interactive value can execute JavaScript and request external resources with the
page's authority.

Mounted code can read or change the document, use browser storage, and make
network requests allowed by the page. Before mounting executable output:

- review the module or chart specification
- configure [Content Security Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CSP)
  and allowed origins
- set byte limits for untrusted or unusually large exports
- pass abort signals to stale loads and mounts
- dispose replaced mounts and page-lifetime resources

An HTML loader and marimo snapshot return inert markup records. Sanitize and
render that markup under the application's HTML policy. Integrity verification
does not make HTML safe to insert into the document.

Use [Deploy an export](../guide/deploy) for the operational checklist and the
[export format reference](../reference/export-format) for exact verification
rules and size limits.
