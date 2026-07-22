# Trust and integrity

marimo-export verifies exact bytes. Trust in who produced those bytes comes from the channel that delivers the build's `ExportRef`.

## Integrity chain

A successful remote build returns:

```ts
interface ExportRef {
  readonly key: string;
  readonly sha256: string;
  readonly size: number;
}
```

The key is `marimo-export/indexes/<sha256>.json`. The index records one content-addressed reference for every portable payload:

```text
marimo-export/payloads/sha256/<sha256>
```

Use the build reference when opening a publication:

```ts
const published = await openExport(directorySource("/tmp/finance-export"), {
  ref: build.ref,
});
```

The reader bounds an unanchored `index.json` to 16 MiB by default. A trusted reference supplies its exact expected size. The reader verifies the index size and SHA-256 before UTF-8 decoding or schema validation, then validates the strict `marimo-export.index.v1` shape. Payload bytes are source-bounded to their indexed size and verified before any read returns them.

`openExport()` also exposes `published.ref`, derived from the index bytes it opened. That reference identifies the current bytes. A separately trusted reference anchors those bytes to an earlier authenticated build.

## Retain the build record

The CLI can save a `marimo-export.build.v1` record while publishing:

```bash
marimo-export publish \
  --server http://127.0.0.1:2718/ \
  --notebook /absolute/path/on/server/notebook.py \
  --plan finance.plan.yaml \
  --out /tmp/finance-export \
  --record /tmp/finance.build.json
```

Use that record to anchor later CLI reads:

```bash
marimo-export verify /tmp/finance-export --ref /tmp/finance.build.json
marimo-export inspect /tmp/finance-export --ref /tmp/finance.build.json
marimo-export read /tmp/finance-export baseline summary \
  --format json \
  --ref /tmp/finance.build.json
```

Protect the build record through the same authenticated deployment channel as other release metadata. It contains the server URL, notebook path, reference, and receipt. Credentials stay in environment variables or the calling application.

## Verification modes

### Reader verification

Concurrent unsignaled `bytes()`, `text()`, `json()`, `blob()`, or loader reads of the same payload share one in-flight verified source read. The reader evicts that entry when it settles. A later read goes through the source and verification again. Signaled reads run independently, and byte arrays are returned as defensive copies.

### Full publication verification

`verifyExport()` and `marimo-export verify` read every unique payload reference:

```ts
const result = await verifyExport({
  source: directorySource("/tmp/finance-export"),
  ref: build.ref,
});

if (!result.ok) {
  console.error(result.failures);
}
```

Passing `ref` verifies the index and payload closure. Omitting it checks payloads against the publication's current valid index. Payload failures appear in the returned report, and `bytes` counts successfully verified payload bytes. An unreadable or invalid index, reference mismatch, invalid concurrency, or abort rejects the operation.

### Incremental pull verification

`pullExport()` verifies the source index against the supplied reference, verifies each downloaded payload, and skips a local file after its size and digest match. The function writes `index.json` after the payload closure completes.

## Producer trust

Plans can contain expression sources. Custom exporter definitions and importable exporter references execute Python in the notebook environment. Accept plans from callers who are permitted to execute code in that environment.

marimo's native cell cache is also part of the producer execution boundary. It can restore serialized Python values before marimo-export creates a projection. Cache signature behavior depends on the prepared environment and marimo configuration, and a lean base producer may run without the optional cryptographic verifier. Keep the producer cache store, `__marimo__/cache` by default, writable only by the trusted producer identity. Rebuild it after an untrusted process or user could have modified it.

Publication hashes verify the resulting index and projection bytes. They do not authenticate a Python value that the producer restored from a tampered native cache.

Keep the notebook's served `public` target and its `public/.marimo-export` namespace under the trusted producer identity during staging and lease cleanup. The stage manager owns that namespace and its randomly named lease directories.

The producer reads the saved notebook file and records its SHA-256 in the index. The plan's canonical SHA-256 is also recorded. A consumer can show both digests as provenance, but a digest alone does not establish who authored the notebook or approved the plan.

Remote control uses the marimo server's authentication. Use HTTPS for network connections or forward a loopback-bound server through SSH. The CLI reads `MARIMO_TOKEN` and `MARIMO_SERVER_TOKEN` from its environment.

Reserve the attached kernel for one remote request at a time across all callers. Keep interactive work and other clients idle until the request settles. Marimo's scratchpad disconnect watcher can interrupt the whole attached session. Fresh scenario child runners reset graph state, while request orchestration and process state still use the attached kernel.

Configure a finite marimo session TTL. A disconnect before `kernel-ready` leaves session ownership unknown, so marimo-export cannot safely issue blind shutdown against a possible kiosk attachment. When both the CLI's lease cleanup and `remote.close()` retry fail, `remote.close()` skips managed-session shutdown, closes the local socket, and rejects. The server TTL bounds the disconnected session lifetime. A transfer stage expires through its separate 30-minute lease.

## Active output formats

Hash verification proves that bytes match the reference. The host application still decides how to interpret them.

- JSON, Arrow, Parquet, text, PNG, and raw bytes are data inputs to their selected decoders.
- HTML can run browser behavior when a host inserts it into an active document. Apply the host's sanitization, sandbox, and content security policy.
- Vega-Lite specifications can reference external data or resources. Configure network access through the host application and Vega runtime.
- AnyWidget mounting executes the notebook-authored frontend module. Embedded files, embedded CSS assets, and `data:` module URLs are anchored by the projection payload. Embedded files mount through `blob:` URLs and need that scheme in `script-src`. Literal HTTP module dependencies plus root-relative, HTTP, or HTTPS resources referenced by widget CSS are runtime network inputs outside the verified payload closure. Apply content security policy, cross-origin resource sharing, origin trust, and availability controls to those inputs.
- A custom loader defines the semantics of its `formatId`.

Publish active formats from notebooks and exporters that the host application trusts. Keep the caveat beside the code path that inserts HTML, mounts Vega-Lite or AnyWidget, or invokes a custom loader.

## Published data

The index exposes scenario IDs, complete public input vectors, notebook and plan digests, producer versions, output names, format metadata, payload sizes, and payload digests. Payload files contain the projected notebook results.

Serve the publication through the application's normal public or authenticated data path. A remote transfer stage is temporary, but the pulled publication persists until the publishing system removes it.

## Local publication trees

Keep local source and destination trees under the caller's control while a read, pull, or verification is active. The Node entrypoint rejects ordinary symlinks, non-files, and paths outside the anchored root. An untrusted local process that concurrently replaces directory components remains outside the filesystem contract.

## HTTP sources

`httpSource()` accepts custom headers and a custom Fetch implementation. Keep credentials in headers supplied at runtime. The resolved publication root rejects embedded credentials, query strings, and fragments, which keeps path resolution beneath one explicit root. A browser location or explicit `options.base` may contain its own query or fragment before root resolution.

HTTP reads reject redirects. The built-in source enforces byte limits through `Content-Length` when present and while streaming the response body. Use same-origin publication paths in browsers when possible and configure cross-origin resource sharing deliberately when the files live on another origin.
