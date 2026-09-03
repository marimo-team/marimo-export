# Portable JSON workspace

The private `@marimo-team/portable-json` workspace validates JavaScript values
against the portable JSON contract shared by marimo-export's Python and
TypeScript boundaries. Vite+ bundles it into the public browser package.

```bash
pnpm --filter @marimo-team/portable-json test
```

## Convert a JavaScript value

```ts
import { portableJsonObject } from "@marimo-team/portable-json";

const source = { symbols: ["AAPL", "MSFT"], window: 30 };
const inputs = portableJsonObject(source);

source.window = 90;
console.log(inputs.window); // 30
console.log(Object.isFrozen(inputs)); // true
```

```ts
portableJsonValue(input: unknown, path?: string): JsonValue
portableJsonObject(input: unknown, path?: string): JsonObject
```

Both functions return detached values. Arrays and objects are recursively frozen.
The conversion enforces these cross-language rules:

- nesting depth is at most `256`
- one conversion contains at most `100000` values, including object keys
- numbers are finite and integers stay within JavaScript's safe range
- strings and object keys contain Unicode scalar values
- arrays are dense
- active container cycles fail while repeated references are copied
- own object keys such as `__proto__` remain own data properties

An incompatible input throws `TypeError`. The optional `path` appears in the
bounded diagnostic.

## Parse JSON text

```ts
import { parsePortableJson, parseStrictJson } from "@marimo-team/portable-json";

const response = parsePortableJson('{"status":"ready","rows":2}');
const preflight = parseStrictJson('{"status":"ready"}', 10);
```

```ts
parsePortableJson(source: string): JsonValue
parseStrictJson(source: string, maximumValues?: number): JsonValue
```

`parsePortableJson()` applies the portable value contract and rejects duplicate
decoded object keys. `parseStrictJson()` checks strict JSON syntax and a caller
supplied value limit before a protocol parser applies its own schema.

The root subpath exports `JsonPrimitive`, `JsonValue`, and `JsonObject` as
TypeScript types. It also exports `MAX_JSON_DEPTH`, `MAX_JSON_VALUES`, and the
four functions shown on this page.

## Compose with Zod

[Zod](https://zod.dev/) validates JavaScript and TypeScript values against runtime
schemas. The workspace's optional Zod subpath composes the portable JSON contract
with an existing schema:

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

`losslessRecordSchema()` rejects key collisions introduced by key-schema
transforms.
