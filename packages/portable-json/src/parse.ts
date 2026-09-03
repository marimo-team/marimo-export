import { portableJsonValue } from "./convert.js";
import type { JsonValue } from "./types.js";
import { MAX_JSON_DEPTH, MAX_JSON_VALUES } from "./types.js";

const MAX_JSON_DIAGNOSTIC_STRING = 256;
const MAX_JSON_NUMBER_LEXEME_BYTES = 1_024;

/** Parse one JSON value while rejecting duplicate object keys at every depth. */
export function parseStrictJson(text: string, maximumValues?: number): JsonValue {
  const scanner = new JsonScanner(text, jsonValueLimit(maximumValues));
  scanner.value(0);
  scanner.whitespace();
  if (!scanner.done) scanner.fail("Unexpected content after the JSON value");
  // SAFETY: JsonScanner accepted one complete JSON value before JSON.parse reads the same text.
  return JSON.parse(text) as JsonValue;
}

/** Parse strict JSON and return a detached portable value. */
export function parsePortableJson(text: string): JsonValue {
  return portableJsonValue(parseStrictJson(text, MAX_JSON_VALUES));
}

class JsonScanner {
  readonly #maximumValues: number;
  readonly #text: string;
  #offset = 0;
  #values = 0;

  constructor(text: string, maximumValues: number) {
    this.#text = text;
    this.#maximumValues = maximumValues;
  }

  get done(): boolean {
    return this.#offset === this.#text.length;
  }

  whitespace(): void {
    while (this.#offset < this.#text.length) {
      const code = this.#text.charCodeAt(this.#offset);
      if (code !== 0x20 && code !== 0x09 && code !== 0x0a && code !== 0x0d) return;
      this.#offset += 1;
    }
  }

  value(depth: number): void {
    this.countValue();
    if (depth > MAX_JSON_DEPTH) this.fail("JSON exceeds the maximum nesting depth");
    this.whitespace();
    const token = this.#text[this.#offset];
    if (token === "{") {
      this.object(depth);
      return;
    }
    if (token === "[") {
      this.array(depth);
      return;
    }
    if (token === '"') {
      this.string(false);
      return;
    }
    if (token === "t") {
      this.literal("true");
      return;
    }
    if (token === "f") {
      this.literal("false");
      return;
    }
    if (token === "n") {
      this.literal("null");
      return;
    }
    this.number();
  }

  object(depth: number): void {
    this.#offset += 1;
    this.whitespace();
    if (this.consume("}")) return;
    const keys = new Set<string>();
    while (true) {
      this.whitespace();
      if (this.#text[this.#offset] !== '"') this.fail("Object keys must be strings");
      this.countValue();
      const key = this.string(true);
      if (key === undefined) this.fail("Object keys must be strings");
      if (keys.has(key)) this.fail(`Duplicate object key ${quoteJsonDiagnostic(key)}`);
      keys.add(key);
      this.whitespace();
      if (!this.consume(":")) this.fail("Expected ':' after an object key");
      this.value(depth + 1);
      this.whitespace();
      if (this.consume("}")) return;
      if (!this.consume(",")) this.fail("Expected ',' or '}' in an object");
    }
  }

  array(depth: number): void {
    this.#offset += 1;
    this.whitespace();
    if (this.consume("]")) return;
    while (true) {
      this.value(depth + 1);
      this.whitespace();
      if (this.consume("]")) return;
      if (!this.consume(",")) this.fail("Expected ',' or ']' in an array");
    }
  }

  string(decode: boolean): string | undefined {
    const start = this.#offset;
    this.#offset += 1;
    while (this.#offset < this.#text.length) {
      const code = this.#text.charCodeAt(this.#offset);
      if (code === 0x22) {
        this.#offset += 1;
        if (!decode) return undefined;
        // SAFETY: The scanner has consumed exactly one quoted JSON string.
        return JSON.parse(this.#text.slice(start, this.#offset)) as string;
      }
      if (code < 0x20) this.fail("Unescaped control character in a string");
      if (code === 0x5c) {
        this.#offset += 1;
        const escape = this.#text[this.#offset];
        if (escape === "u") {
          const digits = this.#text.slice(this.#offset + 1, this.#offset + 5);
          if (!/^[0-9a-fA-F]{4}$/.test(digits)) this.fail("Invalid Unicode escape");
          this.#offset += 5;
          continue;
        }
        if (escape === undefined || !'"\\/bfnrt'.includes(escape)) {
          this.fail("Invalid string escape");
        }
      }
      this.#offset += 1;
    }
    this.fail("Unterminated string");
  }

  number(): void {
    const start = this.#offset;
    if (this.#text[this.#offset] === "-") this.#advanceNumber(start);
    const first = this.#text[this.#offset];
    if (first === "0") {
      this.#advanceNumber(start);
    } else if (first !== undefined && first >= "1" && first <= "9") {
      do this.#advanceNumber(start);
      while (isDigit(this.#text[this.#offset]));
    } else {
      this.fail("Expected a JSON value");
    }
    if (this.#text[this.#offset] === ".") {
      this.#advanceNumber(start);
      if (!isDigit(this.#text[this.#offset])) this.fail("Expected a digit after the decimal point");
      do this.#advanceNumber(start);
      while (isDigit(this.#text[this.#offset]));
    }
    if (this.#text[this.#offset] === "e" || this.#text[this.#offset] === "E") {
      this.#advanceNumber(start);
      if (this.#text[this.#offset] === "+" || this.#text[this.#offset] === "-") {
        this.#advanceNumber(start);
      }
      if (!isDigit(this.#text[this.#offset])) this.fail("Expected an exponent digit");
      do this.#advanceNumber(start);
      while (isDigit(this.#text[this.#offset]));
    }
    const value = this.#text.slice(start, this.#offset);
    if (value.includes(".") || /[eE]/u.test(value)) {
      const converted = Number(value);
      if (!decimalLexemeIsIntegral(value) && Number.isInteger(converted)) {
        this.fail("JSON number loses its fractional component as a JavaScript number");
      }
    }
  }

  #advanceNumber(start: number): void {
    this.#offset += 1;
    if (this.#offset - start > MAX_JSON_NUMBER_LEXEME_BYTES) {
      this.fail("JSON number exceeds the maximum lexeme length");
    }
  }

  literal(value: string): void {
    if (!this.#text.startsWith(value, this.#offset)) this.fail(`Expected ${value}`);
    this.#offset += value.length;
  }

  consume(value: string): boolean {
    if (this.#text[this.#offset] !== value) return false;
    this.#offset += 1;
    return true;
  }

  countValue(): void {
    this.#values += 1;
    if (this.#values > this.#maximumValues) this.fail("JSON exceeds the maximum value count");
  }

  fail(message: string): never {
    throw new SyntaxError(`${message} at character ${this.#offset}.`);
  }
}

function decimalLexemeIsIntegral(value: string): boolean {
  let unsigned = value[0] === "-" ? value.slice(1) : value;
  let exponent = 0;
  const exponentIndex = unsigned.search(/[eE]/u);
  if (exponentIndex >= 0) {
    exponent = Number(unsigned.slice(exponentIndex + 1));
    unsigned = unsigned.slice(0, exponentIndex);
  }
  const decimalIndex = unsigned.indexOf(".");
  const fractionDigits = decimalIndex < 0 ? 0 : unsigned.length - decimalIndex - 1;
  const digits =
    decimalIndex < 0
      ? unsigned
      : `${unsigned.slice(0, decimalIndex)}${unsigned.slice(decimalIndex + 1)}`;
  if (/^0+$/u.test(digits)) return true;
  const trailingZeros = countTrailingZeros(digits);
  return exponent - fractionDigits + trailingZeros >= 0;
}

function isDigit(value: string | undefined): boolean {
  return value !== undefined && value >= "0" && value <= "9";
}

function countTrailingZeros(value: string): number {
  let count = 0;
  for (let index = value.length - 1; index >= 0 && value[index] === "0"; index -= 1) {
    count += 1;
  }
  return count;
}

function quoteJsonDiagnostic(value: string): string {
  const bodyLimit = MAX_JSON_DIAGNOSTIC_STRING - 5;
  let body = "";
  let truncated = false;
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    let token: string;
    if (codeUnit === 0x22 || codeUnit === 0x5c) {
      token = `\\${value[index]}`;
    } else if (
      codeUnit <= 0x1f ||
      (codeUnit >= 0x7f && codeUnit <= 0x9f) ||
      (codeUnit >= 0xd800 && codeUnit <= 0xdfff)
    ) {
      if (
        codeUnit >= 0xd800 &&
        codeUnit <= 0xdbff &&
        index + 1 < value.length &&
        value.charCodeAt(index + 1) >= 0xdc00 &&
        value.charCodeAt(index + 1) <= 0xdfff
      ) {
        token = value.slice(index, index + 2);
        index += 1;
      } else {
        token = `\\u${codeUnit.toString(16).padStart(4, "0")}`;
      }
    } else {
      token = value[index]!;
    }
    if (body.length + token.length > bodyLimit) {
      truncated = true;
      break;
    }
    body += token;
  }
  return `"${body}${truncated ? "..." : ""}"`;
}

function jsonValueLimit(input: number | undefined): number {
  if (input === undefined) return MAX_JSON_VALUES;
  if (!Number.isSafeInteger(input) || input <= 0) {
    throw new TypeError("JSON value limits must be positive safe integers.");
  }
  return input;
}
