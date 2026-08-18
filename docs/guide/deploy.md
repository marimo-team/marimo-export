---
title: Deploy an export
description: Verify the export, review executable browser representations, and configure static hosting.
---

# Deploy an export

Verify the complete export before copying it to a static host or handing it to
an agent or service:

```bash
marimo-export verify dist/finance
```

The same reader validates local exports during the producer commit. Running the
command again checks the files selected for deployment.

## Treat producer code as notebook code

`build`, `capture`, and `session NOTEBOOK` execute notebook code with its file,
credential, network, and package access. Review the notebook, custom exporters,
and selected capture session before running these commands.

Pin the notebook environment and external data inputs when the export must be
reproducible. marimo cache entries execute with the notebook's authority when
restored.

Producer installs include cryptographic verification support. marimo's signing
policy and key configuration determine whether a cache is signed and verified.
Configure persistent signing keys and trusted signers when unverified cache
entries are unacceptable.

## Review mounted browser code

AnyWidget, Vega-Lite, and custom loaders can execute JavaScript or request
external resources with the page's authority.

- Review widget modules, chart specifications, and custom loaders.
- Configure Content Security Policy and allowed origins.
- Set output byte limits for untrusted or unusually large exports.
- Abort stale state transitions and dispose replaced mounts.

Opening and verifying `index.json` executes no notebook-authored browser
module. Mounting an interactive representation grants that module page
authority.

## Serve the directory

Serve the notebook export over HTTPS or localhost. Pass `openExport()` the URL
that contains `index.json`. A browser application can consume it from the same
origin or an origin allowed by the deployment policy.

Content-addressed assets can use long-lived immutable cache headers. Choose an
`index.json` cache policy that matches how quickly a replacement export should
become visible.

After deployment, open the application at desktop and narrow widths. Exercise
every state control, inspect browser errors, and confirm that notebook result
requests use the static application origin.
