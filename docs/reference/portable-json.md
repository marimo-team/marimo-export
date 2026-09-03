---
title: Portable JSON
description: Cross-language JSON values, conversion, strict parsing, limits, errors, and the optional Zod adapter.
---

# Portable JSON

`@marimo-team/portable-json` validates values before they cross the Python and
JavaScript boundary used by notebook exports. It normalizes primitive values and
returns detached, frozen copies of valid array and object inputs.

```bash
pnpm add @marimo-team/portable-json
```

```ts
import { parsePortableJson, portableJsonObject } from "@marimo-team/portable-json";

const inputs = portableJsonObject({
  symbols: ["AAPL", "MSFT"],
  window: 30,
});
const response = parsePortableJson('{"status":"ready","rows":2}');
```

Use the package for state inputs, manifest data, descriptor metadata, custom
loader payloads, and other contracts that must agree with Python's portable
JSON implementation.

## Value types

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

Supported inputs use plain JavaScript primitives, arrays, and ordinary objects
that match `JsonValue`. Boxed wrappers such as `new String("value")` are outside
the public input contract.

Strings and object keys must contain Unicode scalar values. An unpaired UTF-16
surrogate fails conversion. Negative zero becomes positive zero.

## Convert JavaScript values

```ts
function portableJsonValue<Input>(input: Input, path?: string): JsonValue;
function portableJsonObject<Input>(input: Input, path?: string): JsonObject;
```

For inputs that match `JsonValue`, `portableJsonValue()` copies every array and
object and freezes each copied container. Mutating the source after conversion
cannot change a container result. When the same source object appears in several
positions, each position receives its own copy. A container cycle raises
`TypeError`.

`portableJsonObject()` applies the same conversion and requires an object at the
root. Its default diagnostic path is `value`. Pass a project noun to identify a
failure:

Given an unknown `candidate` from application input:

```ts
const config = portableJsonObject(candidate, "chart configuration");
```

Conversion preserves reserved own keys such as `__proto__`, `constructor`, and
`toString` as data. It does not mutate `Object.prototype`.

Incompatible values raise `TypeError`. Examples include a function, `undefined`,
a sparse array, NaN, infinity, an unsafe integer, an invalid Unicode string, or
a cycle.

## Conversion limits

```ts
import { MAX_JSON_DEPTH, MAX_JSON_VALUES } from "@marimo-team/portable-json";

console.log(MAX_JSON_DEPTH); // 256
console.log(MAX_JSON_VALUES); // 100000
```

| Limit                            |                     Count |
| -------------------------------- | ------------------------: |
| Maximum nesting depth            | 256 levels below the root |
| Maximum values in one conversion |                   100,000 |

The value count includes the root, every array item, every object value, and
every object key. Conversion checks a large or sparse array before allocating
its output array. Diagnostic paths are bounded so malformed input cannot create
an unbounded error message.

## Parse JSON text

### `parsePortableJson(source)`

```ts
function parsePortableJson(source: string): JsonValue;
```

Parses one strict JSON value, rejects duplicate decoded object keys at every
depth, applies the portable value rules, and returns detached frozen data.

Duplicate detection uses decoded keys. These spellings collide and fail:

```json
{ "name": 1, "\u006eame": 2 }
```

Syntax failures raise `SyntaxError`. A syntactically valid value that violates
the portable contract raises `TypeError`.

### `parseStrictJson(source, maximumValues?)`

```ts
function parseStrictJson(source: string, maximumValues?: number): JsonValue;
```

Use the strict parser when another protocol parser owns its schema and value
policy. It enforces:

- one complete JSON value and JSON whitespace
- unique decoded object keys
- the 256-level depth limit
- the supplied positive safe-integer value limit, or 100,000 by default
- number lexemes of at most 1,024 characters
- preservation of a written fractional component during JavaScript number conversion

`parseStrictJson()` returns the direct `JSON.parse()` value. It does not detach
or freeze that value. It also leaves the portable safe-integer, Unicode scalar,
and negative-zero rules to the caller. Use `parsePortableJson()` when those
cross-language guarantees are required.

An invalid `maximumValues` raises `TypeError`. Invalid JSON, a duplicate key, or
a breached parser limit raises `SyntaxError` with a bounded character offset.

## Compose with Zod

[Zod](https://zod.dev/) builds runtime schemas whose TypeScript types follow the
validated result. Install it when the portable value must compose with another
Zod contract:

```bash
pnpm add @marimo-team/portable-json zod
```

```ts
import {
  jsonObjectSchema,
  jsonValueSchema,
  losslessRecordSchema,
} from "@marimo-team/portable-json/zod";
import { z } from "zod";

const config = jsonObjectSchema.parse({ theme: "dark" });

const values = losslessRecordSchema(z.string().trim(), jsonValueSchema).parse({
  status: "ready",
});
```

### `jsonValueSchema`

A `z.ZodType<JsonValue>` that applies `portableJsonValue()` as a transform.
Conversion failures become one custom Zod issue.

### `jsonObjectSchema`

A `z.ZodType<JsonObject>` that applies `portableJsonObject()` as a transform.

### `losslessRecordSchema(keySchema, valueSchema)`

```ts
function losslessRecordSchema<KeySchema extends z.ZodType<string>, ValueSchema extends z.ZodType>(
  keySchema: KeySchema,
  valueSchema: ValueSchema,
): z.ZodType<Readonly<Record<string, z.output<ValueSchema>>>>;
```

Parses each own object entry through the supplied schemas and returns a frozen
record. It rejects non-object input, more than 100,000 source entries, an invalid
entry, or two source keys that transform to the same parsed key.

Key-collision rejection prevents a key transform such as trimming or
lowercasing from silently overwriting an earlier entry. Reserved keys remain
own data properties.

Zod is an optional peer. Importing `@marimo-team/portable-json` at the package
root does not load or require Zod.

## Use in a custom output loader

Decode bytes with fatal UTF-8 handling, parse portable JSON, then validate the
representation-specific fields and ranges. Portable conversion owns the shared
primitive and container rules. The representation loader owns application
meaning.

[Define a custom representation](representations#define-a-custom-representation)
shows the complete producer and browser pair.
