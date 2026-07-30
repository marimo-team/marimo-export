import type { MediaType } from "./types.js";
import { NotebookExportError } from "./types.js";

const TOKEN = /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/u;
const PRINTABLE_ASCII = /^[\u0020-\u007e]+$/u;
const encoder = new TextEncoder();

export function parseMediaType(raw: string): MediaType {
  try {
    if (
      typeof raw !== "string" ||
      encoder.encode(raw).byteLength > 1_024 ||
      !PRINTABLE_ASCII.test(raw) ||
      raw !== raw.trim()
    ) {
      throw new TypeError("Media type must contain at most 1,024 printable ASCII bytes.");
    }
    const parser = new MediaTypeParser(raw);
    return parser.parse();
  } catch (error) {
    if (error instanceof NotebookExportError) throw error;
    throw new NotebookExportError("export_invalid", "Export media type is invalid.", {
      cause: error,
    });
  }
}

class MediaTypeParser {
  readonly #raw: string;
  #offset = 0;

  constructor(raw: string) {
    this.#raw = raw;
  }

  parse(): MediaType {
    const type = this.token().toLowerCase();
    this.expect("/");
    const subtype = this.token().toLowerCase();
    const parameters = new Map<string, string>();
    this.whitespace();
    while (!this.done) {
      this.expect(";");
      this.whitespace();
      const name = this.token().toLowerCase();
      this.whitespace();
      this.expect("=");
      this.whitespace();
      const value = this.peek() === '"' ? this.quoted() : this.token();
      if (parameters.has(name)) throw new TypeError("Media type parameter is repeated.");
      parameters.set(name, value);
      this.whitespace();
    }
    return Object.freeze({
      raw: this.#raw,
      essence: `${type}/${subtype}`,
      type,
      subtype,
      parameters: new FrozenMap(parameters),
    });
  }

  token(): string {
    const start = this.#offset;
    while (!this.done && TOKEN.test(this.#raw[this.#offset]!)) this.#offset += 1;
    if (this.#offset === start) throw new TypeError("Media type token is missing.");
    return this.#raw.slice(start, this.#offset);
  }

  quoted(): string {
    this.expect('"');
    let result = "";
    while (!this.done) {
      const character = this.#raw[this.#offset++]!;
      if (character === '"') return result;
      if (character === "\\") {
        if (this.done) throw new TypeError("Media type quoted string is truncated.");
        result += this.#raw[this.#offset++]!;
        continue;
      }
      if (character.charCodeAt(0) < 0x20 || character.charCodeAt(0) === 0x7f) {
        throw new TypeError("Media type quoted string contains a control character.");
      }
      result += character;
    }
    throw new TypeError("Media type quoted string is unterminated.");
  }

  whitespace(): void {
    while (this.peek() === " " || this.peek() === "\t") this.#offset += 1;
  }

  expect(value: string): void {
    if (this.#raw[this.#offset] !== value) throw new TypeError(`Expected ${value}.`);
    this.#offset += 1;
  }

  peek(): string | undefined {
    return this.#raw[this.#offset];
  }

  get done(): boolean {
    return this.#offset >= this.#raw.length;
  }
}

class FrozenMap<K, V> implements ReadonlyMap<K, V> {
  readonly #map: Map<K, V>;

  constructor(map: Map<K, V>) {
    this.#map = map;
    Object.freeze(this);
  }

  get size(): number {
    return this.#map.size;
  }

  get(key: K): V | undefined {
    return this.#map.get(key);
  }

  has(key: K): boolean {
    return this.#map.has(key);
  }

  entries(): MapIterator<[K, V]> {
    return this.#map.entries();
  }

  keys(): MapIterator<K> {
    return this.#map.keys();
  }

  values(): MapIterator<V> {
    return this.#map.values();
  }

  forEach(callbackfn: (value: V, key: K, map: ReadonlyMap<K, V>) => void, thisArg?: unknown): void {
    for (const [key, value] of this.#map) callbackfn.call(thisArg, value, key, this);
  }

  [Symbol.iterator](): MapIterator<[K, V]> {
    return this.#map[Symbol.iterator]();
  }
}
