---
title: Deploy a notebook export
description: Verify files, configure static hosting, and preserve the integrity and browser trust boundaries.
---

# Deploy a notebook export

A deployed notebook export is a directory whose `index.json` and declared
assets remain available at stable HTTP URLs. Verify the directory before upload,
then exercise the deployed consumer against the final origin.

## Preview the static files

Build the [first notebook export](getting-started.md), then serve its parent
directory:

```bash
python -m http.server 8000 --directory dist
```

`http://127.0.0.1:8000/report/index.json` should return canonical JSON. Pass
`http://127.0.0.1:8000/report/` to `openExport()`.

Run the complete verifier before copying files:

```bash
marimo-export verify dist/report
```

## Configure the host

The host must preserve paths and bytes exactly. It may compress responses in
transit because readers verify the decoded body.

| Resource                               | Recommended cache policy                                        |
| -------------------------------------- | --------------------------------------------------------------- |
| Immutable asset under `assets/`        | Long-lived immutable caching                                    |
| Versioned export instance `index.json` | Long-lived immutable caching                                    |
| Replaceable standalone `index.json`    | Revalidate according to the application's freshness requirement |
| Prepared manifest `current` route      | `no-store` or equivalent revalidation                           |

Serve the application and its export from one origin when possible. A separate
origin must allow the application origin through [Cross-Origin Resource Sharing
(CORS)](https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS). Use the browser
API's custom `fetch` option when requests require credentials or an origin
allowlist. Do not place bearer credentials in the export URL query because that
query is copied to every asset request.

## Configure executable representations

[Content Security Policy
(CSP)](https://developer.mozilla.org/en-US/docs/Web/HTTP/CSP) controls resources
that mounted outputs can execute or create.

| Representation                    | Policy capability that may be required  |
| --------------------------------- | --------------------------------------- |
| Embedded AnyWidget module         | `script-src blob:`                      |
| Remote AnyWidget module           | Its HTTPS script origin                 |
| AnyWidget styles                  | The application's accepted style policy |
| Image loader                      | `img-src blob:`                         |
| Vega-Lite external data or images | Each declared network or image origin   |

An AnyWidget module loaded from an HTTP URL remains outside the export asset
closure. The export verifies the stored URL record, while the remote server owns
the module bytes returned at mount time.

## Separate integrity from publisher trust

`openExport()` validates canonical `index.json`. Loading an output verifies the
selected asset. `NotebookExport.verify()` and `marimo-export verify` read every
declared asset.

These checks prove consistency with `index.json`. Authenticate the publisher
through the deployment origin, signed release artifacts, or another mechanism
owned by your application. marimo computation-cache signing protects producer
cache restoration and is separate from notebook export authentication.

## Verify the deployed application

After upload:

1. Open the application at desktop and narrow widths.
2. Exercise every exported state and one interaction in each mounted output.
3. Confirm that all notebook result requests use the deployed static origin.
4. Confirm that exported state changes open no kernel or WebSocket connection.
5. Inspect console errors and failed requests.
6. Verify the same deployed directory through an independent download when the
   host can transform uploaded files.

Use [Troubleshooting](troubleshooting.md) for CORS, CSP, integrity, state, and
loader failures.
