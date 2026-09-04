---
title: Portable JSON
description: The shared Python and JavaScript value domain, canonical encoding, limits, and published interfaces.
---

# Portable JSON

Portable JSON is the value contract shared by Python producers and browser
consumers. It contains null, booleans, Unicode scalar strings, finite numbers,
arrays, and string-keyed objects that preserve their meaning across Python and
JavaScript.

The Python package publishes runtime conversion and canonical encoding through
`marimo_export.wire`. The browser package publishes the matching TypeScript
types and applies the value checks inside its reader, prepared-manifest, JSON
loader, and snapshot APIs.

## Value contract

```ts
import type { JsonObject, JsonPrimitive, JsonValue } from "@marimo-team/marimo-export";

const inputs: JsonObject = {
  symbols: ["AAPL", "MSFT"],
  window: 30,
};
```

```ts
type JsonPrimitive = string | number | boolean | null;
type JsonValue = JsonPrimitive | readonly JsonValue[] | JsonObject;

interface JsonObject {
  readonly [key: string]: JsonValue;
}
```

A portable value can contain:

- `null`, booleans, strings, and finite numbers
- integers from `-9007199254740991` through `9007199254740991`
- dense arrays of portable values
- objects with string keys and portable values

Strings and object keys contain Unicode scalar values. An unpaired UTF-16
surrogate is invalid. Negative zero becomes positive zero during portable
conversion.

The TypeScript declarations describe values that already satisfy the contract.
Export indexes, prepared manifests, state-resolution inputs, and built-in loader
values are validated by the owning operation. A custom loader owns validation of
its representation bytes before returning a `JsonValue`.

## Limits

| Boundary                                  |                         Limit |
| ----------------------------------------- | ----------------------------: |
| Container depth below the root            |                           256 |
| Values in one tree, including object keys |                       100,000 |
| Integer magnitude                         | JavaScript safe-integer range |
| Strict JSON number lexeme                 |              1,024 characters |

Conversion rejects sparse arrays, active container cycles, nonfinite numbers,
unsafe integers, invalid Unicode strings, and incompatible object types. A
repeated container reference is copied at each position. Object keys such as
`__proto__` remain own data properties.

## Convert values in Python

```python
from marimo_export.wire import JsonValue, portable_json

source = {"symbols": ["AAPL", "MSFT"], "window": 30}
value: JsonValue = portable_json(source, "inputs")
```

```python
portable_json(value: object, path: str = "value") -> JsonValue
```

`portable_json()` returns detached mutable Python data. Mappings become
dictionaries. Non-string, non-byte sequences become lists. `path` labels a
bounded validation error and has no effect on the returned value.

Some reader, specification, inspection, and host-observation records expose
recursively immutable aliases with tuples and immutable mappings. Persisted
repository observation records expose a read-only top-level mapping with newly
decoded mutable nested containers. Their `to_dict()` or `to_value()` methods
return detached mutable data.

## Canonical JSON in Python

```python
from marimo_export.wire import (
    canonical_json_bytes,
    canonical_json_sha256,
    parse_canonical_json,
    state_fingerprint,
)

inputs = {"items": [1, -0.0], "label": "ready"}
assert canonical_json_bytes(inputs) == b'{"items":[1,0],"label":"ready"}'
assert state_fingerprint(inputs) == canonical_json_sha256(inputs)
```

Canonical JSON has one UTF-8 byte representation for each supported value:

- object keys sort by Unicode code point
- insignificant whitespace is absent
- JSON literals are lowercase
- finite numbers use the ECMAScript-compatible spelling
- negative zero encodes as `0`

`parse_canonical_json()` accepts a string or contiguous byte buffer. It rejects
invalid UTF-8, duplicate decoded keys, nonportable values, noncanonical key or
number spelling, whitespace, and any input whose canonical re-encoding differs.

## Browser protocol boundaries

The published browser package applies portable JSON at these entry points:

| Operation                                          | Accepted or returned value              |
| -------------------------------------------------- | --------------------------------------- |
| `NotebookExport.resolve(inputs)`                   | Complete `JsonObject` input vector      |
| `ExportState.resolve(patch)`                       | Sparse root-input `JsonObject`          |
| `jsonLoader()`                                     | Detached recursively frozen `JsonValue` |
| Prepared manifest parsing                          | Complete manifest input object          |
| `PreparedStateController.updateInputs(patch)`      | Sparse root-input object                |
| `PreparedStateController.updateControl(id, value)` | One portable control value              |
| `NotebookExportError.details`                      | Optional frozen diagnostic object       |

Import browser types from the public package root:

```ts
import type { JsonObject, JsonPrimitive, JsonValue } from "@marimo-team/marimo-export";
```

These are compile-time types. The published browser package has no standalone
portable JSON converter or parser. A custom representation loader must validate
its decoded value before returning it.

Use [Format records and errors](python/format-records-and-errors) for the Python
signatures. Use [Browser reader](browser/reader) and [Prepared
publications](browser/prepared-publications) for the TypeScript operations that
validate portable values.
