# Trust and integrity

marimo-export verifies that a publication's cache objects match `index.json`. The authenticated capture response establishes the index received from the notebook session. The deployment channel and host application protect that index and decide who may execute active output formats.

## Publication integrity

Each format entry contains one asset reference:

```json
{
  "key": "<opaque-marimo-key>/return.bin",
  "sha256": "<digest>",
  "size": 184
}
```

Python and browser readers perform this sequence:

1. Validate `marimo-export.publication.v1`.
2. Resolve `cache/<asset.key>` as a portable relative path.
3. Bound the index and each asset by caller limits. The Python reader also bounds the declared unique-asset closure.
4. Verify the exact MessagePack bytes against size and SHA-256.
5. Decode the marimo `BlobAsset` envelope.
6. Confirm media type, format identifier, and metadata against the index, then validate the optional filename.
7. Pass the inner `data` bytes to the selected decoder or loader.

Corruption fails before JSON parsing, image decoding, table decoding, or browser mounting.

`uv run marimo-export verify PUBLICATION` applies the sequence to every unique asset referenced by the index.

`index.json` is the trust root for a static read. Asset digests detect corruption or substitution relative to that index. Protect the index through the same authenticated deployment path as the rest of the application.

On Windows, keep the publication directory tree stable until the Python reader completes its second file-identity check. It rejects symbolic links, junctions, and other reparse points, then fails when its validation checks detect a changed path.

## Capture authority

Capture uses an edit-capable marimo session. A specification can evaluate trusted Python expressions and import custom exporters. Treat capture authority as permission to run Python in the notebook environment.

The running kernel can access the notebook process's packages, files, credentials, network, accelerators, and external services. Apply the marimo server's authentication and network policy to that environment. Use HTTPS across a network or keep the server loopback-bound behind an SSH tunnel.

The `access_token` in a marimo URL is removed from the normalized server address. marimo-export keeps credentials out of publication indexes, receipts, stdout, stderr, and exception messages.

## Cache trust

marimo's persistent cache can restore Python values and `BlobAsset` objects before marimo-export transfers them. Protect the configured cache store with the same identity that runs the notebook kernel.

Asset digests prove that transferred cache objects match the captured index. They cannot prove the origin of a value restored from a cache that another identity could modify. Clear or replace a cache store after untrusted write access.

## Variant effects

Applying a variant sends frontend values to existing marimo UI controls and runs reactive dependents. marimo-export restores the starting UI vector after every variant and after failures.

Restoration cannot reverse writes to databases, files, network services, imported-module state, random generators, native-library globals, or background tasks. Use idempotent notebook effects, transactional resources, or isolated targets when capturing variants.

## Active formats

Integrity verification establishes exact bytes. The host application chooses how those bytes are interpreted.

- JSON and text use reader decoders. Arrow, Parquet, PNG, and raw bytes remain verified data for the consuming application. A custom loader may decode a trusted Arrow or Parquet projection.
- HTML can execute browser behavior after insertion into an active document. Apply the application's sanitization, sandbox, and content security policy.
- Vega-Lite specifications can request external data or resources. Configure network access through the Vega runtime.
- AnyWidget mounting executes the notebook-authored frontend module. Allow required module and style sources through content security policy and dispose mounted views when they leave the page.
- A custom loader defines the parsing and execution semantics of its format ID.

Opening, navigation, integrity verification, and generic byte reads stay separate from format-loader decoding and mounting. Keep `load()` and `mount()` behind an explicit application decision.

## HTTP publication sources

Browser publication roots accept HTTP or HTTPS. They reject embedded credentials, query strings, fragments, redirects, path traversal, and bodies that exceed configured limits.

Use same-origin publication paths where practical. For a protected cross-origin publication, supply request headers through the application and configure cross-origin resource sharing for the reader origin.
