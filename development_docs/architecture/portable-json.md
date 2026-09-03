# Portable JSON

Portable JSON is the value boundary shared by Python producers and JavaScript
consumers. It accepts values whose type and numeric identity survive the
language boundary, then provides deterministic bytes for fingerprints and wire
protocols.

`packages/portable-json` owns JavaScript and TypeScript validation. Python owns
the matching value and canonical byte rules in `_json.py`, exposed through
`marimo_export.wire`.

## Value contract

A portable value contains:

- `null` or `None`
- booleans
- Unicode scalar strings
- finite numbers
- arrays or Python sequences
- objects or Python mappings with string keys

The shared limits are:

| Boundary                                  | Limit                                                                  |
| ----------------------------------------- | ---------------------------------------------------------------------- |
| Nesting depth                             | 256                                                                    |
| Values in one tree, including object keys | 100,000                                                                |
| Integer magnitude                         | JavaScript safe integer range, from `-(2**53 - 1)` through `2**53 - 1` |
| Strict JSON number lexeme                 | 1,024 characters                                                       |

Conversion rejects `NaN`, infinity, lone Unicode surrogates, unsupported object
types, and integers outside the safe range. Negative zero becomes zero in a
portable value. JavaScript arrays must be dense. Active container cycles fail,
while a repeated reference is copied into each position.

Object keys such as `__proto__` remain data properties. Conversion does not use
them to mutate an object's prototype.

## Conversion and parsing are different operations

The TypeScript root package exposes:

```text
portableJsonValue(input, path?)
portableJsonObject(input, path?)
parseStrictJson(source, maximumValues?)
parsePortableJson(source)
```

`portableJsonValue()` and `portableJsonObject()` detach the accepted tree and
recursively freeze the JavaScript result. `parseStrictJson()` checks one JSON
text before `JSON.parse` runs. It rejects duplicate decoded object keys, excess
nesting, excess values, oversized number lexemes, and fractional source numbers
that JavaScript would round to an integer. `parsePortableJson()` adds the
portable value checks and returns a detached frozen value.

Python `portable_json()` returns a detached Python value. Public immutable
records convert its lists and mappings to tuples and read-only mappings when
their own contract requires immutability.

Use conversion for an in-memory value. Use strict parsing for untrusted JSON
text. Use canonical parsing when the exact byte spelling is part of an identity
or protocol.

## Canonical bytes

`marimo_export.wire.canonical_json_bytes()` emits UTF-8 JSON with:

- object keys sorted by Unicode code point
- no insignificant whitespace
- lowercase JSON literals
- ECMAScript-compatible finite number spelling
- negative zero normalized to `0`

`parse_canonical_json()` accepts a string or contiguous byte buffer, applies the
portable value contract, re-encodes the value, and rejects any input whose bytes
differ from the canonical form.

`canonical_json_sha256()` hashes those exact bytes. `state_fingerprint()` applies
the same operation to one complete input object. The repository and browser can
therefore resolve the same state without sharing Python objects or JavaScript
object identity.

Read [Identities and protocols](identities-and-protocols.md) before changing a
canonical value that contributes to a state, plan, or export identity.

## Ownership by runtime

| Owner                                   | Responsibility                                                                           |
| --------------------------------------- | ---------------------------------------------------------------------------------------- |
| `packages/portable-json/src/convert.ts` | JavaScript value conversion, detachment, freezing, and bounds                            |
| `packages/portable-json/src/parse.ts`   | Strict JSON scanning and duplicate-key rejection                                         |
| `packages/portable-json/src/types.ts`   | Shared JavaScript types and limits                                                       |
| `packages/portable-json/src/zod.ts`     | Optional Zod schemas and transformed-key collision checks                                |
| `_json.py`                              | Python value validation, strict decoding, canonical number spelling, and canonical bytes |
| `wire.py`                               | Public Python conversion, canonical parsing, hashing, and state fingerprints             |
| `packages/browser/src/schema.ts`        | Export-specific parsing after portable JSON validation                                   |

The package root has no runtime peer dependency. The optional
The workspace package's `zod` subpath requires Zod and lets repository code
compose portable values with a larger Zod schema. `losslessRecordSchema()`
rejects two source keys when a key transform maps them to the same parsed key.

## Change and validation rules

A portable JSON change affects every state fingerprint and every canonical
protocol that embeds the changed value. Update these surfaces together:

1. Python validation and canonical serialization
2. JavaScript conversion and strict parsing
3. Python and TypeScript types
4. canonical JSON fixtures under `tests/fixtures/canonical-json`
5. export index and prepared-manifest consumers
6. malformed, depth, count, numeric, Unicode, and duplicate-key tests
7. packed root and Zod subpath consumers

Run:

```bash
uv run pytest -q packages/python/tests/test_json.py \
  packages/python/tests/test_portable_json_fixture.py
pnpm --filter @marimo-team/portable-json test
pnpm --filter @marimo-team/marimo-export test -- cross-language
pnpm --filter @marimo-team/portable-json test:package
```
