import { expect, test } from "vite-plus/test";
import { z } from "zod";

import { jsonObjectSchema, jsonValueSchema, losslessRecordSchema } from "../src/zod.js";

test("Zod schemas use the portable normalizer", () => {
  const source = JSON.parse('{"__proto__":{"count":1},"rows":[1,-0]}');
  const value = jsonObjectSchema.parse(source);

  expect(Object.hasOwn(value, "__proto__")).toBe(true);
  expect(value.rows).toEqual([1, 0]);
  expect(Object.isFrozen(value)).toBe(true);
  expect(() => jsonValueSchema.parse(2 ** 53)).toThrow("JavaScript safe range");
});

test("lossless records preserve reserved keys", () => {
  const schema = losslessRecordSchema(z.string(), z.number());
  const value = schema.parse(JSON.parse('{"__proto__":1,"safe":2}'));

  expect(Object.hasOwn(value, "__proto__")).toBe(true);
  expect(value.__proto__).toBe(1);
});

test("lossless records reject keys that collide after parsing", () => {
  const schema = losslessRecordSchema(z.string().trim().toLowerCase(), z.number());

  expect(() => schema.parse({ " A ": 1, a: 2 })).toThrow("collide after parsing");
});
