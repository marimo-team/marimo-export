---
title: Deploy a notebook export
description: Verify files, configure static hosting, and preserve the integrity and browser trust boundaries.
---

# Deploy a notebook export

A deployed notebook export is a directory whose `index.json` and declared
assets remain available at stable HTTP URLs. Verify the directory before upload,
then exercise the deployed consumer against the final origin.

## Preview the static files

Build the [first notebook export](getting-started), then serve its parent
directory:

```bash
python -m http.server 8000 --bind 127.0.0.1 --directory dist
```

`http://127.0.0.1:8000/report/index.json` should return canonical JSON. Pass
`http://127.0.0.1:8000/report/` to `openExport()`.

Run the complete verifier before copying files:

```bash
uv run marimo-export verify dist/report
```

Upload or copy `dist/report/` as one directory. Deploy the written export, not
the local export repository.

On POSIX systems, marimo-export creates directories with owner-only access and
files with owner read and write access. A static server running as another user
may need the deployment tool to assign its ownership or permissions. Apply that
policy after the final directory commit. `--replace` installs a new directory
and does not preserve the old directory's mode, owner, access-control lists, or
extended attributes.

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

| Representation                        | Policy capability that may be required                                     |
| ------------------------------------- | -------------------------------------------------------------------------- |
| Embedded AnyWidget module             | `script-src blob:`                                                         |
| AnyWidget module stored as a data URL | `script-src data:`                                                         |
| Remote AnyWidget module               | Its HTTPS script origin                                                    |
| AnyWidget styles                      | Inline styles or another policy that accepts an inserted `<style>` element |
| Image loader                          | `img-src blob:`                                                            |
| Vega-Lite external data or images     | Each declared network or image origin                                      |

An AnyWidget module loaded from an HTTP URL remains outside the export asset
closure. The export verifies the stored URL record, while the remote server owns
the module bytes returned at mount time. A remote module must use a JavaScript
media type, allow the application origin through CORS, and satisfy the page's
HTTPS and request policies. The browser reader's custom `fetch` handles export
requests. It does not intercept AnyWidget module imports or Vega-Lite data and
image requests.

HTML loaders and marimo snapshot records return inert markup. Apply the
application's sanitization and rendering policy before inserting that markup
into the document.

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

Use [Troubleshooting](troubleshooting) for CORS, CSP, integrity, state, and
loader failures.
