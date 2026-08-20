# @marimo-team/portable-json

Validate values before they cross Python and JavaScript boundaries.

```ts
import { parsePortableJson, portableJsonObject } from "@marimo-team/portable-json";

const inputs = portableJsonObject({ symbols: ["AAPL", "MSFT"], window: 30 });
const response = parsePortableJson('{"status":"ready","rows":2}');
```

## Convert values

```ts
portableJsonValue(input: unknown, path?: string): JsonValue
portableJsonObject(input: unknown, path?: string): JsonObject
```

Both functions return detached recursively frozen values. They enforce these
cross-language rules:

- nesting depth is at most `256`
- one conversion contains at most `100000` values, including object keys
- numbers are finite and integers stay within JavaScript's safe range
- strings and object keys contain Unicode scalar values
- arrays are dense
- active container cycles fail while repeated references are copied
- own object keys such as `__proto__` remain own data properties

An incompatible input throws `TypeError`. The optional `path` appears in the
bounded diagnostic.

## Parse text

```ts
parsePortableJson(source: string): JsonValue
parseStrictJson(source: string, maximumValues?: number): unknown
```

`parsePortableJson` applies the portable value contract and rejects duplicate
decoded object keys. `parseStrictJson` provides the strict syntax preflight for
protocol parsers that apply their own schema and value limit.

The package also exports `JsonPrimitive`, `JsonValue`, `JsonObject`,
`UnparsedJsonValue`, `MAX_JSON_DEPTH`, and `MAX_JSON_VALUES`.

## Use Zod schemas

Install Zod when a schema must compose with an existing Zod contract:

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
const values = losslessRecordSchema(z.string(), jsonValueSchema).parse({
  status: "ready",
});
```

`losslessRecordSchema` rejects key collisions introduced by key-schema
transforms.
