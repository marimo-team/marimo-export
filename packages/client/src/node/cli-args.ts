import { parseArgs } from "node:util";

import type { JsonObject } from "../types.js";

type OptionType = "string" | "boolean";
export type OptionDefinitions = Readonly<Record<string, { readonly type: OptionType }>>;

export interface CommandArguments {
  readonly values: Readonly<Record<string, string | boolean | undefined>>;
  readonly positionals: readonly string[];
}

export class CliUsageError extends Error {
  readonly details: JsonObject | undefined;

  constructor(message: string, cause?: unknown, details?: JsonObject) {
    super(message, cause === undefined ? undefined : { cause });
    this.name = "CliUsageError";
    this.details = details;
  }
}

export function parseCommand(
  args: readonly string[],
  options: OptionDefinitions,
): CommandArguments {
  try {
    const parsed = parseArgs({
      args: [...args],
      options,
      allowPositionals: true,
      strict: true,
      tokens: true,
    });
    const seen = new Set<string>();
    for (const token of parsed.tokens) {
      if (token.kind !== "option") continue;
      if (seen.has(token.name)) throw new CliUsageError(`Option --${token.name} was repeated.`);
      seen.add(token.name);
    }
    return {
      values: parsed.values as Readonly<Record<string, string | boolean | undefined>>,
      positionals: parsed.positionals,
    };
  } catch (error) {
    if (error instanceof CliUsageError) throw error;
    throw new CliUsageError(error instanceof Error ? error.message : String(error), error);
  }
}

export function commonOptions(extra: OptionDefinitions = {}): OptionDefinitions {
  return {
    help: { type: "boolean" },
    json: { type: "boolean" },
    "timeout-ms": { type: "string" },
    ...extra,
  };
}

export function remoteOptions(extra: OptionDefinitions = {}): OptionDefinitions {
  return commonOptions({
    server: { type: "string" },
    notebook: { type: "string" },
    session: { type: "string" },
    ...extra,
  });
}

export function requiredOption(args: CommandArguments, name: string): string {
  const value = optionalOption(args, name);
  if (value === undefined) throw new CliUsageError(`--${name} is required.`);
  return value;
}

export function optionalOption(args: CommandArguments, name: string): string | undefined {
  const value = args.values[name];
  if (value === undefined) return undefined;
  if (typeof value !== "string" || value.length === 0) {
    throw new CliUsageError(`--${name} requires a non-empty value.`);
  }
  return value;
}

export function booleanOption(args: CommandArguments, name: string): boolean {
  return args.values[name] === true;
}

export function positiveIntegerOption(
  args: CommandArguments,
  name: string,
  fallback: number,
): number {
  const input = optionalOption(args, name);
  if (input === undefined) return fallback;
  const value = Number(input);
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new CliUsageError(`--${name} must be a positive integer.`);
  }
  return value;
}

export function nonNegativeIntegerOption(
  args: CommandArguments,
  name: string,
  fallback: number,
): number {
  const input = optionalOption(args, name);
  if (input === undefined) return fallback;
  const value = Number(input);
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new CliUsageError(`--${name} must be a non-negative integer.`);
  }
  return value;
}

export function boundedLimitOption(args: CommandArguments): number {
  const value = positiveIntegerOption(args, "limit", 50);
  if (value > 500) throw new CliUsageError("--limit must be at most 500.");
  return value;
}

export function timeoutOption(args: CommandArguments): number {
  return positiveIntegerOption(args, "timeout-ms", 5 * 60_000);
}

export function concurrencyOption(args: CommandArguments): number {
  const value = positiveIntegerOption(args, "concurrency", 8);
  if (value > 64) throw new CliUsageError("--concurrency must be at most 64.");
  return value;
}

export function exactPositionals(args: CommandArguments, count: number): void {
  if (args.positionals.length !== count) {
    throw new CliUsageError(`Expected ${count} positional argument${count === 1 ? "" : "s"}.`);
  }
}

export function noPositionals(args: CommandArguments): void {
  exactPositionals(args, 0);
}
