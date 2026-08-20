import { z } from "zod";

import { portableJsonObject, portableJsonValue } from "./convert.js";
import type { JsonObject, JsonValue } from "./types.js";
import { MAX_JSON_VALUES } from "./types.js";

const unknownValue = z.unknown();

export const jsonValueSchema: z.ZodType<JsonValue> = unknownValue.transform((value, context) => {
  try {
    return portableJsonValue(value);
  } catch (error) {
    context.issues.push({
      code: "custom",
      input: value,
      message: errorMessage(error),
    });
    return z.NEVER;
  }
});

export const jsonObjectSchema: z.ZodType<JsonObject> = unknownValue.transform((value, context) => {
  try {
    return portableJsonObject(value);
  } catch (error) {
    context.issues.push({
      code: "custom",
      input: value,
      message: errorMessage(error),
    });
    return z.NEVER;
  }
});

export const losslessRecordSchema = <
  KeySchema extends z.ZodType<string>,
  ValueSchema extends z.ZodType,
>(
  keySchema: KeySchema,
  valueSchema: ValueSchema,
): z.ZodType<Readonly<Record<string, z.output<ValueSchema>>>> =>
  unknownValue.transform((value, context) => {
    if (!isObjectRecord(value)) {
      context.issues.push({ code: "custom", input: value, message: "Expected an object record" });
      return z.NEVER;
    }
    const sourceEntries = Object.entries(value);
    if (sourceEntries.length > MAX_JSON_VALUES) {
      context.issues.push({
        code: "custom",
        input: value,
        message: `Object record exceeds ${MAX_JSON_VALUES} entries`,
      });
      return z.NEVER;
    }
    const entries: Array<readonly [string, z.output<ValueSchema>]> = [];
    const parsedKeys = new Set<string>();
    for (const [key, item] of sourceEntries) {
      const parsedKey = keySchema.safeParse(key);
      const parsedValue = valueSchema.safeParse(item);
      if (!parsedKey.success || !parsedValue.success) {
        context.issues.push({
          code: "custom",
          input: item,
          path: [key],
          message: "Invalid record entry",
        });
        return z.NEVER;
      }
      if (parsedKeys.has(parsedKey.data)) {
        context.issues.push({
          code: "custom",
          input: key,
          path: [key],
          message: `Record keys collide after parsing as ${JSON.stringify(parsedKey.data)}`,
        });
        return z.NEVER;
      }
      parsedKeys.add(parsedKey.data);
      entries.push([parsedKey.data, parsedValue.data]);
    }
    return Object.freeze(Object.fromEntries(entries));
  });

function isObjectRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.prototype.toString.call(value) === "[object Object]"
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Expected a portable JSON value";
}
