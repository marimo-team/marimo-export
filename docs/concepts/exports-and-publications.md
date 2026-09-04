---
title: Update an application with new exports
description: Choose between one fixed export URL and a stable application URL that follows newer exports.
---

# Update an application with new exports

Serve a notebook export directly when one URL identifies the complete result.
Its `index.json` and assets form one immutable set of files.

When an application needs one stable URL while new exports arrive, serve a
prepared manifest at that URL. The manifest names one immutable export and its
selected state. Together, the manifest and opened export form a **prepared
publication**.

```mermaid
flowchart LR
    route[Stable application URL]
    manifest[Prepared manifest]
    export[Immutable notebook export]

    route --> manifest --> export
```

## What each object keeps available

| Object               | Lifetime                                                                              |
| -------------------- | ------------------------------------------------------------------------------------- |
| Prepared state       | Reusable producer data for one complete input vector                                  |
| Prepared export      | Leased export generation for one exact `ExportSpec`                                   |
| Notebook export      | Portable `index.json` and assets opened by consumers                                  |
| Prepared manifest    | Small JSON record that selects an export URL, identity, inputs, and state fingerprint |
| Prepared publication | Application lifecycle that keeps a selected export and state available                |

Python and browser APIs both expose a `PreparedPublication` type with different
fields and owners. The Python object retains a prepared export plus application
metadata. The browser object joins a validated manifest, opened notebook export,
and selected exported state.

## Serve one export directly

Serve a notebook export directly when one URL can identify the complete current
result. Configure a replaceable `index.json` for revalidation and its
content-addressed assets for long-lived caching.

## Keep one URL while exports change

Use a prepared publication when the application needs a stable current route and
immutable export instances. The producer keeps the last successful prepared
export available. The current route returns a prepared manifest. Immutable routes
serve that generation's index and assets.

The browser opens and validates a new export before showing it. If loading fails,
the previous export stays visible. If refreshes overlap, only the newest may
replace the current view. Dispose resources created by an older refresh.

## Trust the manifest source

The prepared manifest pins the export's canonical `index.json` bytes by identity.
Asset verification detects changed content after selection.

The application still authenticates the manifest route and export origin. An
export on another origin also needs a matching [Cross-Origin Resource Sharing
(CORS)](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS) policy.

Related: [Verify and trust an export](integrity-and-trust) separates file
verification from publisher trust. [Serve a prepared
publication](../guide/prepared-publications) covers the complete workflow. The
[browser prepared reference](../reference/browser/prepared-publications) defines
controller, refresh, cancellation, and error contracts.
