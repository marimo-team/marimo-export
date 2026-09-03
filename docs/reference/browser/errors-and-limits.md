---
title: Browser errors and limits
description: Error families, codes, byte and allocation limits, cancellation behavior, browser requirements, and recovery paths.
---

# Browser errors and limits

The browser package separates immutable export failures, prepared manifest and
query failures, application port failures, and cancellation. Handle the family
that owns the failed operation.

The partial handling example assumes `operation()`, `reportExportFailure()`, and
`reportPreparedFailure()` belong to the application:

```ts
import { isNotebookExportError, type NotebookExportError } from "@marimo-team/marimo-export";
import { isPreparedAbort, isPreparedExportError } from "@marimo-team/marimo-export/prepared";

try {
  await operation();
} catch (error) {
  if (isPreparedAbort(error)) return;
  if (isNotebookExportError(error)) {
    reportExportFailure(error.code, error.details, error.cause);
    return;
  }
  if (isPreparedExportError(error)) {
    reportPreparedFailure(error.code, error.cause);
    return;
  }
  throw error;
}
```

## `NotebookExportError`

```ts
class NotebookExportError extends Error {
  readonly code: NotebookExportErrorCode;
  readonly details: JsonObject | undefined;
  readonly cause: unknown;
}
```

Construct it as `new NotebookExportError(code, message, { cause, details })`.
The constructor validates the code and message, converts `details` to frozen
portable JSON, then freezes the error. An unknown code or non-string message
raises `TypeError`.

`isNotebookExportError(value)` recognizes the versioned global brand across
iframes and separately bundled package copies. It also checks the public name,
message, code, and optional details. Inaccessible or invalid properties make the
guard return `false`.

### Reader error codes

| Code                            | Operation and meaning                                                                   | Common recovery                                                                                                |
| ------------------------------- | --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `abort`                         | An export operation or output load lost caller authority                                | Ignore stale work or retry with a live signal                                                                  |
| `asset_invalid`                 | A BlobAsset MessagePack envelope or its descriptor agreement is invalid                 | Rebuild or replace the export                                                                                  |
| `decode_failed`                 | Native framing, a snapshot parser, or the selected loader rejected data                 | Confirm the producer and loader versions, then rebuild the representation                                      |
| `integrity_failed`              | Declared size, observed size, SHA-256, or Web Crypto verification failed                | Stop using the artifact and redeploy from a verified source                                                    |
| `loader_ambiguous`              | More than one registered loader accepts the output                                      | Narrow media-type predicates or pass one explicit loader                                                       |
| `loader_invalid`                | A loader is malformed, throws in `accepts()`, or returns a non-boolean acceptance value | Correct the loader contract                                                                                    |
| `loader_unavailable`            | No loader accepts the output codec and media type                                       | Import the matching loader and its peer runtime                                                                |
| `output_not_found`              | An output name is absent from the selected state                                        | Inspect `outputNames` and use a published name                                                                 |
| `output_representation_changed` | One output name changes codec or media type across states                               | Rebuild with one stable representation per output                                                              |
| `export_invalid`                | The index schema, shape, values, names, descriptors, or state fingerprints are invalid  | Regenerate the export with a compatible producer                                                               |
| `export_noncanonical`           | `index.json` parses but its exact bytes are not canonical JSON                          | Regenerate the index through marimo-export                                                                     |
| `read_failed`                   | Fetch, response status, body availability, or stream reading failed                     | Check the URL, CORS, authentication, deployment, and network                                                   |
| `read_limit_exceeded`           | An index, asset, or complete verification exceeds its byte budget                       | Inspect the declared size, then raise a caller limit only when the artifact is trusted and memory is available |
| `state_input_invalid`           | A complete vector or sparse patch has invalid values or input names                     | Compare the object with `inputNames`                                                                           |
| `state_not_found`               | An authored state alias is absent                                                       | Inspect each state's `aliases` array                                                                           |
| `state_unavailable`             | A valid complete input vector is absent from this export                                | Choose an exported state or publish another export                                                             |

`details` is bounded diagnostic context. Its fields vary by failure site. Common
fields include output name, codec, media type, requested and available names,
path, response status, declared and observed bytes, byte limits, expected and
observed digests, and input key sets. Branch on `code` first and treat additional
fields as optional.

An error thrown by a loader passes through unchanged when it is already a
branded `NotebookExportError`. Another loader failure becomes `decode_failed`.
An abort-shaped loader failure becomes `abort` and retains its source as
`cause`.

## `PreparedExportError`

```ts
type PreparedExportErrorCode =
  "manifest_invalid" | "manifest_read_failed" | "query_ambiguous" | "query_miss";

class PreparedExportError extends Error {
  readonly code: PreparedExportErrorCode;
  readonly cause: unknown;
  constructor(
    code: PreparedExportErrorCode,
    message: string,
    options?: { readonly cause?: unknown },
  );
}
```

| Code                   | Meaning                                                                                      | Recovery                                                             |
| ---------------------- | -------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `manifest_invalid`     | The document, identity, export URL, input vector, fingerprint, or control binding is invalid | Fix the route or republish a matching manifest and export            |
| `manifest_read_failed` | The manifest request, response, stream, or byte limit failed                                 | Check routing, CORS, authentication, response status, and body size  |
| `query_ambiguous`      | One query text matches more than one typed exported value                                    | Use an unambiguous state control or change the exported input domain |
| `query_miss`           | A recognized query parameter is repeated or matches no exported value                        | Supply one value whose text matches the exported domain              |

`isPreparedExportError()` recognizes a frozen, branded prepared error across
package copies. Construction validates the code and message and freezes the
result. Prepared controller state resolution can still raise
`NotebookExportError`. `PreparedStatePort` can raise an application error. A
transition failure followed by a restoration failure raises `AggregateError`
with both causes.

## Cancellation

`isPreparedAbort(value)` recognizes:

- a `DOMException` named `AbortError`
- an `Error` named `AbortError`
- a `NotebookExportError` with code `abort`

Prepared APIs preserve an `Error` or `DOMException` used as an abort reason.
Another reason becomes a `DOMException` named `AbortError`.

Cancellation removes stale commit authority and asks cooperative work to stop.
Browser dynamic imports, Parquet decoding, and renderer work can continue after
the caller receives an abort. Application ports must check the signal before
visible commit and clean up late or staged resources.

## Default and hard limits

### Reader and manifest limits

| Boundary                                 |                         Limit | Behavior                                                                   |
| ---------------------------------------- | ----------------------------: | -------------------------------------------------------------------------- |
| `index.json` body                        |                        16 MiB | `openExport()` raises `read_limit_exceeded` before or during the body read |
| `index.json` parsed values               |                     2,000,000 | Strict parsing rejects a larger document                                   |
| One output asset, default caller limit   |                       512 MiB | `ExportOutput.load()` and `verify()` use this when `maxBytes` is absent    |
| One output asset, hard caller limit      |           2,147,483,647 bytes | A larger `maxBytes` raises `TypeError`                                     |
| Complete verification, default aggregate |                         2 GiB | Declared unique asset bytes are checked before any verification fetch      |
| Prepared manifest body                   |                       256 KiB | Fetch rejects a larger declared or observed body                           |
| Prepared `export_url`                    |             8,192 UTF-8 bytes | Manifest parsing rejects an empty or larger value                          |
| Prepared polling                         | `0`, or 250 through 60,000 ms | Zero disables polling. Other values outside the range fail parsing         |

`maxBytes` must be a positive safe integer. `maxTotalBytes` must be a
non-negative safe integer. A value of zero is valid for `maxTotalBytes` and
allows verification only when the export has no assets.

Producer and local-reader defaults are narrower at 64 MiB per asset and 512 MiB
across one export. A browser limit is a consumer memory policy and does not
expand what a standard producer writes.

### Format and loader limits

| Boundary                                         |                                                Limit |
| ------------------------------------------------ | ---------------------------------------------------: |
| Export input, output, version, and filename text | 255 UTF-8 bytes, with field-specific character rules |
| Control object ID                                |                                    1,024 UTF-8 bytes |
| Control path                                     |                                            256 steps |
| Descriptor provenance text                       |                                    2,048 UTF-8 bytes |
| BlobAsset metadata                               |                            256 KiB of canonical JSON |
| Media type                                       |                          1,024 printable ASCII bytes |
| One declared descriptor asset                    |                                  2,147,483,647 bytes |
| marimo snapshot values                           |                                            2,000,000 |
| NumPy header                                     |                                                1 MiB |
| NumPy typed-array length                         |                               4,294,967,295 elements |
| Arrow LZ4 decompressed buffer                    |                                              512 MiB |
| AnyWidget page module definitions                |                                                1,024 |
| AnyWidget external module URL                    |                                    8,192 UTF-8 bytes |
| AnyWidget data URL media type                    |                                    1,024 UTF-8 bytes |

[Export format](../export-format) defines field shapes and producer limits.
[Portable JSON](../portable-json) defines its separate 256-level and
100,000-value limits.

## Browser requirements

Opening and loading use these web platform capabilities:

- the [Fetch API](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API) and readable response streams
- strict `TextEncoder` and `TextDecoder` support
- [`SubtleCrypto.digest()`](https://developer.mozilla.org/en-US/docs/Web/API/SubtleCrypto/digest) with SHA-256
- `AbortController`, `AbortSignal`, and `AbortSignal.throwIfAborted()`
- `URL`, `Object.hasOwn()`, frozen objects, and `structuredClone()`
- typed arrays, including BigInt typed arrays for 64-bit NumPy integers

Mounts can also require the DOM, Blob and object URL APIs, dynamic module import,
canvas or SVG support, and the capabilities of a selected peer runtime.

Web Crypto is available in a [secure
context](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts).
Serve an application over HTTPS or from a browser-recognized local development
origin. A missing Web Crypto implementation raises `integrity_failed`.

The package manifest does not declare a browser-version matrix. Check these
capabilities in every supported application browser and run mounted runtime
tests for each required loader.

## Diagnose common failures

### The export cannot be read

Inspect the resolved base and request URLs, response status, CORS response
headers, credentials, and response body. `NotebookExportError.details.path` and
optional `status` identify the failed export object.

### The index is invalid or noncanonical

Regenerate the directory with marimo-export. Reformatting, pretty-printing, or
appending a newline changes canonical `index.json` bytes and its identity.

### A loader is unavailable

Compare `output.codec` and `output.mediaType.essence` with the [loader
catalog](loaders#loader-catalog). Install the listed peer beside
`@marimo-team/marimo-export`.

### A mount is blocked

Inspect the browser console and the deployed [Content Security Policy
(CSP)](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CSP). Check Blob
module, script origin, style element, image, data, font, and network permissions
used by the selected representation.

### A state or query cannot resolve

Inspect `inputNames`, `states().map(state => state.inputs)`, and each state's
`aliases`. Query strings match only values present in that finite domain. A new
input vector requires a new export or a Python service.
