import { resolve } from "node:path";

import { parse as parseYaml } from "yaml";

import packageMetadata from "../../package.json" with { type: "json" };

import { openExport } from "../reader.js";
import type { ExportOutput, NotebookExport } from "../reader.js";
import { parseExportRef } from "../schema.js";
import { httpSource } from "../source.js";
import type { ExportRef, ExportSource, JsonValue } from "../types.js";
import { MarimoExportError } from "../types.js";
import { validateExportPlan } from "../remote/plan.js";
import type { ExportPlan } from "../remote/plan.js";
import { connectRemote } from "../remote/session.js";
import type { ConnectRemoteOptions, Remote, RemoteTarget } from "../remote/session.js";
import {
  createRemoteBuildRecord,
  parseRemoteBuildRecord,
  REMOTE_BUILD_RECORD_SCHEMA,
} from "./build-record.js";
import type { RemoteBuildRecord } from "./build-record.js";
import {
  booleanOption,
  boundedLimitOption,
  CliUsageError,
  commonOptions,
  concurrencyOption,
  exactPositionals,
  noPositionals,
  nonNegativeIntegerOption,
  optionalOption,
  parseCommand,
  positiveIntegerOption,
  remoteOptions,
  requiredOption,
  timeoutOption,
} from "./cli-args.js";
import type { CommandArguments } from "./cli-args.js";
import { readDocument } from "./cli-input.js";
import {
  preflightExportDestination,
  preflightOutputFile,
  preflightPublicationRecord,
  pullRemote,
  verifyExport,
  writeOutputFileAtomic,
} from "./checkout.js";
import { directorySource } from "./source.js";

const CLI_SCHEMA = "marimo-export.cli.v1" as const;
const CLI_VERSION = packageMetadata.version;
const DEFAULT_MAX_BYTES = 1_000_000;
const CLEANUP_TIMEOUT_MS = 10_000;

export interface CliIO {
  stdout(data: string | Uint8Array): void;
  stderr(text: string): void;
  stdin?(signal?: AbortSignal): Promise<string>;
}

export interface CliResult {
  readonly exitCode: number;
}

export async function runCli(
  argv: readonly string[],
  io: CliIO = defaultIO(),
  signal?: AbortSignal,
): Promise<CliResult> {
  const command = argv[0];
  const json = argv.includes("--json");
  try {
    if (command === undefined || command === "help" || command === "--help") {
      io.stdout(helpText());
      return { exitCode: 0 };
    }
    if (command === "version" || command === "--version") {
      io.stdout(`${CLI_VERSION}\n`);
      return { exitCode: 0 };
    }
    const args = argv.slice(1);
    if (args.includes("--help")) {
      io.stdout(helpText());
      return { exitCode: 0 };
    }
    switch (command) {
      case "publish": {
        const parsed = parseCommand(
          args,
          remoteOptions({
            plan: { type: "string" },
            out: { type: "string" },
            record: { type: "string" },
            concurrency: { type: "string" },
          }),
        );
        await withCommandTimeout(parsed, signal, (commandSignal) =>
          publishCommand(parsed, io, commandSignal),
        );
        break;
      }
      case "build": {
        const parsed = parseCommand(
          args,
          remoteOptions({
            plan: { type: "string" },
            record: { type: "string" },
          }),
        );
        await withCommandTimeout(parsed, signal, (commandSignal) =>
          buildCommand(parsed, io, commandSignal),
        );
        break;
      }
      case "pull": {
        const parsed = parseCommand(
          args,
          commonOptions({
            server: { type: "string" },
            out: { type: "string" },
            concurrency: { type: "string" },
          }),
        );
        await withCommandTimeout(parsed, signal, (commandSignal) =>
          pullCommand(parsed, io, commandSignal),
        );
        break;
      }
      case "inspect": {
        const parsed = parseCommand(
          args,
          commonOptions({
            ref: { type: "string" },
            scenario: { type: "string" },
            offset: { type: "string" },
            limit: { type: "string" },
          }),
        );
        await withCommandTimeout(parsed, signal, (commandSignal) =>
          inspectCommand(parsed, io, commandSignal),
        );
        break;
      }
      case "read": {
        const parsed = parseCommand(
          args,
          commonOptions({
            ref: { type: "string" },
            format: { type: "string" },
            "max-bytes": { type: "string" },
            out: { type: "string" },
          }),
        );
        await withCommandTimeout(parsed, signal, (commandSignal) =>
          readCommand(parsed, io, commandSignal),
        );
        break;
      }
      case "verify": {
        const parsed = parseCommand(
          args,
          commonOptions({
            ref: { type: "string" },
            concurrency: { type: "string" },
          }),
        );
        const ok = await withCommandTimeout(parsed, signal, (commandSignal) =>
          verifyCommand(parsed, io, commandSignal),
        );
        return { exitCode: ok ? 0 : 1 };
      }
      case "describe": {
        const parsed = parseCommand(args, remoteOptions());
        await withCommandTimeout(parsed, signal, (commandSignal) =>
          describeCommand(parsed, io, commandSignal),
        );
        break;
      }
      default:
        throw new CliUsageError(`Unknown command ${JSON.stringify(command)}.`);
    }
    return { exitCode: 0 };
  } catch (error) {
    const cancelled = aborted(error, signal);
    const detail = errorRecord(error, cancelled);
    if (json) {
      io.stderr(
        `${JSON.stringify({ schema: CLI_SCHEMA, command: command ?? null, error: detail })}\n`,
      );
    } else {
      io.stderr(`${detail.code}: ${detail.message}\n`);
      if (error instanceof CliUsageError) io.stderr("Run marimo-export help for usage.\n");
    }
    return { exitCode: cancelled ? 130 : error instanceof CliUsageError ? 2 : 1 };
  }
}

async function publishCommand(
  args: CommandArguments,
  io: CliIO,
  signal?: AbortSignal,
): Promise<void> {
  noPositionals(args);
  const planPath = requiredOption(args, "plan");
  const destination = requiredOption(args, "out");
  if (destination === "-") throw new CliUsageError("--out requires a directory path.");
  const concurrency = concurrencyOption(args);
  const record = recordOption(args);
  const { server, target } = validateRemoteArguments(args);
  const recordNotebook =
    record === undefined ? undefined : recordTarget(target, "publish --record").notebook;
  signal?.throwIfAborted();
  const plan = await readPlan(planPath, io, signal);
  await preflightExportDestination(destination);
  if (record !== undefined) {
    try {
      await preflightPublicationRecord(record, destination);
    } catch (error) {
      if (
        error instanceof MarimoExportError &&
        error.message === "--record must be outside the publication directory."
      ) {
        throw new CliUsageError(error.message, error);
      }
      throw error;
    }
  }
  const remote = await commandRemote(args, signal);
  const data = await usingRemote(remote, async () => {
    progress(io, "Building export cache.\n");
    const build = await remote.build(plan, readOptions(signal));
    if (record !== undefined && recordNotebook !== undefined) {
      await saveRecord(
        record,
        createRemoteBuildRecord({ server, notebook: recordNotebook, build }),
      );
    }
    progress(io, "Pulling projection payloads.\n");
    const transfer = await pullRemote(remote, build.ref, {
      into: destination,
      concurrency,
      ...(signal === undefined ? {} : { signal }),
    });
    progress(io, "Verifying local publication.\n");
    const verification = await verifyExport({
      source: directorySource(destination),
      ref: build.ref,
      ...(signal === undefined ? {} : { signal }),
    });
    if (!verification.ok) {
      throw new MarimoExportError("integrity_failed", "Published files failed local verification.");
    }
    return { build, transfer, verification };
  });
  writeData(io, "publish", data, booleanOption(args, "json"));
}

async function buildCommand(
  args: CommandArguments,
  io: CliIO,
  signal?: AbortSignal,
): Promise<void> {
  noPositionals(args);
  const planPath = requiredOption(args, "plan");
  const record = recordOption(args);
  const { server, target } = validateRemoteArguments(args);
  const notebook = recordTarget(target, "build").notebook;
  signal?.throwIfAborted();
  const plan = await readPlan(planPath, io, signal);
  if (record !== undefined) await preflightOutputFile(record);
  const remote = await commandRemote(args, signal);
  const build = await usingRemote(remote, async () => {
    progress(io, "Building export cache.\n");
    const result = await remote.build(plan, readOptions(signal));
    const durable = createRemoteBuildRecord({ server, notebook, build: result });
    await saveRecord(record, durable);
    return durable;
  });
  writeBuild(io, build);
}

async function pullCommand(args: CommandArguments, io: CliIO, signal?: AbortSignal): Promise<void> {
  exactPositionals(args, 1);
  const destination = requiredOption(args, "out");
  if (destination === "-") throw new CliUsageError("--out requires a directory path.");
  const concurrency = concurrencyOption(args);
  signal?.throwIfAborted();
  const timeoutMs = timeoutOption(args);
  const trustedServerInput = optionalOption(args, "server");
  const auth = authentication();
  if (hasAuthentication(auth) && trustedServerInput === undefined) {
    throw new CliUsageError("Authenticated pull requires --server URL matching the build record.");
  }
  const trustedServer =
    trustedServerInput === undefined ? undefined : normalizeServer(trustedServerInput);
  const build = parseBuildRecord(await readJson(args.positionals[0]!, io, "remote build", signal));
  if (trustedServer !== undefined && trustedServer !== build.server) {
    throw new CliUsageError("--server must match the server recorded by the build.");
  }
  await preflightExportDestination(destination);
  const remote = await connectRemote({
    server: build.server,
    target: build.target,
    ...auth,
    timeoutMs,
    connectTimeoutMs: timeoutMs,
    ...(signal === undefined ? {} : { signal }),
  });
  const transfer = await usingRemote(remote, async () => {
    progress(io, "Pulling projection payloads.\n");
    return pullRemote(remote, build.ref, {
      into: destination,
      concurrency,
      ...(signal === undefined ? {} : { signal }),
    });
  });
  writeData(io, "pull", transfer, booleanOption(args, "json"));
}

async function inspectCommand(
  args: CommandArguments,
  io: CliIO,
  signal?: AbortSignal,
): Promise<void> {
  exactPositionals(args, 1);
  const offset = nonNegativeIntegerOption(args, "offset", 0);
  const limit = boundedLimitOption(args);
  const requested = optionalOption(args, "scenario");
  const source = sourceArgument(args.positionals[0]!);
  const ref = await optionalRef(args, io, signal);
  const published = await openPublished(source, ref, signal);
  const publication = publicationSummary(published);
  const data =
    requested === undefined
      ? (() => {
          const scenarios = published.scenarios();
          return {
            ...publication,
            page: page(offset, limit, scenarios.length),
            scenarios: scenarios.slice(offset, offset + limit).map((scenario) => ({
              id: scenario.id,
              inputs: scenario.inputs,
              outputCount: scenario.outputs().length,
            })),
          };
        })()
      : (() => {
          const scenario = published.scenario(requested);
          const outputs = scenario.outputs();
          return {
            ...publication,
            scenario: { id: scenario.id, inputs: scenario.inputs },
            page: page(offset, limit, outputs.length),
            outputs: outputs.slice(offset, offset + limit).map(outputSummary),
          };
        })();
  writeData(io, "inspect", data, booleanOption(args, "json"));
}

async function readCommand(args: CommandArguments, io: CliIO, signal?: AbortSignal): Promise<void> {
  exactPositionals(args, 3);
  const [sourceValue, scenarioId, outputName] = args.positionals as [string, string, string];
  const format = optionalOption(args, "format");
  const maxBytes = positiveIntegerOption(args, "max-bytes", DEFAULT_MAX_BYTES);
  const destination = optionalOption(args, "out");
  if (destination === "-") throw new CliUsageError("--out requires a file path.");
  const source = sourceArgument(sourceValue);
  const ref = await optionalRef(args, io, signal);
  if (destination !== undefined) await preflightOutputFile(destination);
  const published = await openPublished(source, ref, signal);
  const scenario = published.scenario(scenarioId);
  const output = scenario.output(outputName, format);
  if (output.ref.size > maxBytes) {
    throw new MarimoExportError(
      "output_too_large",
      `Output declares ${output.ref.size} bytes, above --max-bytes ${maxBytes}.`,
      {
        details: {
          scenario: scenario.id,
          output: output.name,
          formatName: output.formatName,
          declaredBytes: output.ref.size,
          maxBytes,
        },
      },
    );
  }
  const provenance = {
    ...publicationSummary(published),
    scenario: { id: scenario.id, inputs: scenario.inputs },
    output: outputSummary(output),
  };
  if (destination !== undefined) {
    const bytes = await output.bytes(readOptions(signal));
    await writeOutputFileAtomic(destination, bytes);
    writeData(
      io,
      "read",
      { ...provenance, path: resolve(destination) },
      booleanOption(args, "json"),
    );
    return;
  }

  if (isJson(output.mediaType)) {
    const data = await output.json(readOptions(signal));
    if (booleanOption(args, "json")) writeData(io, "read", { ...provenance, data }, true);
    else io.stdout(`${JSON.stringify(data, null, 2)}\n`);
    return;
  }
  if (isText(output.mediaType)) {
    if (booleanOption(args, "json")) {
      const text = await output.text(readOptions(signal));
      writeData(io, "read", { ...provenance, data: text }, true);
    } else {
      const bytes = await output.bytes(readOptions(signal));
      validateUtf8(bytes, output.name);
      io.stdout(bytes);
    }
    return;
  }
  throw new CliUsageError(
    `${output.formatId} is binary. Pass --out FILE to write its verified payload.`,
  );
}

async function verifyCommand(
  args: CommandArguments,
  io: CliIO,
  signal?: AbortSignal,
): Promise<boolean> {
  exactPositionals(args, 1);
  const concurrency = concurrencyOption(args);
  const source = sourceArgument(args.positionals[0]!);
  const ref = await optionalRef(args, io, signal);
  const result = await verifyExport({
    source,
    ...(ref === undefined ? {} : { ref }),
    concurrency,
    ...(signal === undefined ? {} : { signal }),
  });
  writeData(io, "verify", result, booleanOption(args, "json"));
  return result.ok;
}

async function describeCommand(
  args: CommandArguments,
  io: CliIO,
  signal?: AbortSignal,
): Promise<void> {
  noPositionals(args);
  const remote = await commandRemote(args, signal);
  const data = await usingRemote(remote, async () => {
    const description = await remote.describe(readOptions(signal));
    return { session: remote.session, description };
  });
  writeData(io, "describe", data, booleanOption(args, "json"));
}

async function commandRemote(args: CommandArguments, signal?: AbortSignal): Promise<Remote> {
  const { server, target, timeoutMs } = validateRemoteArguments(args);
  const options: ConnectRemoteOptions = {
    server,
    target,
    ...authentication(),
    timeoutMs,
    connectTimeoutMs: timeoutMs,
    ...(signal === undefined ? {} : { signal }),
  };
  return connectRemote(options);
}

function validateRemoteArguments(args: CommandArguments): {
  server: string;
  target: RemoteTarget;
  timeoutMs: number;
} {
  const notebook = optionalOption(args, "notebook");
  const sessionId = optionalOption(args, "session");
  if ((notebook === undefined) === (sessionId === undefined)) {
    throw new CliUsageError("Pass exactly one of --notebook PATH or --session ID.");
  }
  const target: RemoteTarget = notebook === undefined ? { sessionId: sessionId! } : { notebook };
  return {
    server: normalizeServer(requiredOption(args, "server")),
    target,
    timeoutMs: timeoutOption(args),
  };
}

type NotebookTarget = Extract<RemoteTarget, { readonly notebook: string }>;

function recordTarget(target: RemoteTarget, command: "build" | "publish --record"): NotebookTarget {
  if (typeof target.notebook === "string") return target as NotebookTarget;
  if (command === "build") {
    throw new CliUsageError(
      "build requires --notebook PATH so pull can open a fresh session. Use publish --session ID for a same-connection build and pull.",
    );
  }
  throw new CliUsageError(
    "publish --record requires --notebook PATH so the saved build can be reopened. Omit --record when publishing through --session ID.",
  );
}

async function usingRemote<T>(remote: Remote, operation: () => Promise<T>): Promise<T> {
  let result: T | undefined;
  let failure: unknown;
  try {
    result = await operation();
  } catch (error) {
    failure = error;
  }
  try {
    await remote.close({ timeoutMs: CLEANUP_TIMEOUT_MS });
  } catch (error) {
    failure ??= error;
  }
  if (failure !== undefined) throw failure;
  return result as T;
}

async function openPublished(
  source: ExportSource,
  ref: ExportRef | undefined,
  signal?: AbortSignal,
): Promise<NotebookExport> {
  return openExport(source, {
    ...(ref === undefined ? {} : { ref }),
    ...(signal === undefined ? {} : { signal }),
  });
}

function sourceArgument(value: string): ExportSource {
  try {
    if (/^https?:\/\//i.test(value)) {
      const url = new URL(value);
      if (url.username.length > 0 || url.password.length > 0) {
        throw new CliUsageError("HTTP sources must not contain credentials.");
      }
      return httpSource(url);
    }
    if (value.includes("://")) throw new CliUsageError("SOURCE must be a directory or HTTP URL.");
    return directorySource(value);
  } catch (error) {
    if (error instanceof CliUsageError) throw error;
    throw new CliUsageError(`Invalid export source ${JSON.stringify(value)}.`, error);
  }
}

async function optionalRef(
  args: CommandArguments,
  io: CliIO,
  signal?: AbortSignal,
): Promise<ExportRef | undefined> {
  const path = optionalOption(args, "ref");
  if (path === undefined) return undefined;
  const input = await readJson(path, io, "export ref", signal);
  try {
    if (
      typeof input === "object" &&
      input !== null &&
      !Array.isArray(input) &&
      (input as Record<string, unknown>).schema === REMOTE_BUILD_RECORD_SCHEMA
    ) {
      return parseRemoteBuildRecord(input).ref;
    }
    return parseExportRef(input);
  } catch (error) {
    throw new CliUsageError(`Export reference ${JSON.stringify(path)} is invalid.`, error);
  }
}

async function readPlan(path: string, io: CliIO, signal?: AbortSignal): Promise<ExportPlan> {
  const text = await readText(path, io, signal);
  let input: unknown;
  try {
    input = parseYaml(text);
  } catch (error) {
    throw new CliUsageError(`Plan ${JSON.stringify(path)} is not valid YAML or JSON.`, error);
  }
  try {
    return validateExportPlan(input);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const details =
      error instanceof MarimoExportError ? { ...error.details, file: path } : { file: path };
    throw new CliUsageError(`Plan ${JSON.stringify(path)} is invalid: ${message}`, error, details);
  }
}

async function readJson(
  path: string,
  io: CliIO,
  label: string,
  signal?: AbortSignal,
): Promise<unknown> {
  try {
    return JSON.parse(await readText(path, io, signal)) as unknown;
  } catch (error) {
    if (error instanceof CliUsageError || error instanceof MarimoExportError) throw error;
    throw new CliUsageError(`${label} ${JSON.stringify(path)} is not valid JSON.`, error);
  }
}

async function readText(path: string, io: CliIO, signal?: AbortSignal): Promise<string> {
  try {
    const stdin =
      io.stdin === undefined ? undefined : (readSignal?: AbortSignal) => io.stdin!(readSignal);
    return await readDocument(path, stdin, signal);
  } catch (error) {
    if (error instanceof CliUsageError || error instanceof MarimoExportError) throw error;
    throw new MarimoExportError(
      "source_read_failed",
      `Failed to read ${path === "-" ? "standard input" : JSON.stringify(path)}.`,
      { cause: error },
    );
  }
}

async function saveRecord(path: string | undefined, build: RemoteBuildRecord): Promise<void> {
  if (path === undefined) return;
  await writeOutputFileAtomic(
    path,
    new TextEncoder().encode(`${JSON.stringify(build, null, 2)}\n`),
  );
}

function recordOption(args: CommandArguments): string | undefined {
  const path = optionalOption(args, "record");
  if (path === "-") throw new CliUsageError("--record requires a file path.");
  return path;
}

function parseBuildRecord(input: unknown): RemoteBuildRecord {
  try {
    return parseRemoteBuildRecord(input);
  } catch (error) {
    const reason = error instanceof Error ? ` ${error.message}` : "";
    throw new CliUsageError(`Remote build record is invalid.${reason}`, error);
  }
}

function normalizeServer(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch (error) {
    throw new CliUsageError("--server must be an absolute HTTP or HTTPS URL.", error);
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new CliUsageError("--server must use HTTP or HTTPS.");
  }
  if (url.username.length > 0 || url.password.length > 0) {
    throw new CliUsageError("--server must not contain credentials.");
  }
  if (url.search.length > 0 || url.hash.length > 0) {
    throw new CliUsageError("--server must not contain a query or fragment.");
  }
  return new URL(url.pathname.endsWith("/") ? url.href : `${url.href}/`).toString();
}

function publicationSummary(published: NotebookExport) {
  return {
    ref: published.ref,
    notebook: published.notebook,
    planSha256: published.planSha256,
    producer: published.producer,
  };
}

function page(offset: number, limit: number, total: number) {
  const returned = Math.max(0, Math.min(limit, total - offset));
  const following = offset + returned;
  return {
    offset,
    limit,
    total,
    returned,
    nextOffset: returned > 0 && following < total ? following : null,
  };
}

function outputSummary(output: ExportOutput) {
  return {
    name: output.name,
    formatName: output.formatName,
    formatId: output.formatId,
    mediaType: output.mediaType,
    metadata: output.metadata,
    ref: output.ref,
  };
}

function isJson(mediaType: string | null): boolean {
  const normalized = normalizedMediaType(mediaType);
  return normalized === "application/json" || normalized?.endsWith("+json") === true;
}

function isText(mediaType: string | null): boolean {
  return normalizedMediaType(mediaType)?.startsWith("text/") === true;
}

function normalizedMediaType(mediaType: string | null): string | undefined {
  return mediaType?.split(";", 1)[0]?.trim().toLowerCase();
}

function validateUtf8(bytes: Uint8Array, outputName: string): void {
  try {
    new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (error) {
    throw new MarimoExportError(
      "decode_failed",
      `Output ${JSON.stringify(outputName)} is not valid UTF-8 text.`,
      { cause: error },
    );
  }
}

function authentication(): Pick<ConnectRemoteOptions, "authToken" | "serverToken"> {
  const authToken = process.env.MARIMO_TOKEN;
  const serverToken = process.env.MARIMO_SERVER_TOKEN;
  return {
    ...(authToken === undefined || authToken.length === 0 ? {} : { authToken }),
    ...(serverToken === undefined || serverToken.length === 0 ? {} : { serverToken }),
  };
}

function hasAuthentication(value: ReturnType<typeof authentication>): boolean {
  return value.authToken !== undefined || value.serverToken !== undefined;
}

function readOptions(signal: AbortSignal | undefined) {
  return signal === undefined ? {} : { signal };
}

function writeData(io: CliIO, command: string, data: unknown, json: boolean): void {
  const value = json ? { schema: CLI_SCHEMA, command, data } : data;
  io.stdout(`${JSON.stringify(value, null, 2)}\n`);
}

function writeBuild(io: CliIO, build: RemoteBuildRecord): void {
  io.stdout(`${JSON.stringify(build, null, 2)}\n`);
}

function progress(io: CliIO, message: string): void {
  io.stderr(message);
}

function errorRecord(
  error: unknown,
  cancelled: boolean,
): { code: string; message: string; details?: JsonValue } {
  if (cancelled) {
    return {
      code: "cancelled",
      message: error instanceof Error ? error.message : "Command cancelled.",
    };
  }
  if (error instanceof CliUsageError) {
    return {
      code: "usage_error",
      message: error.message,
      ...(error.details === undefined ? {} : { details: error.details }),
    };
  }
  if (error instanceof MarimoExportError) {
    return {
      code: error.code,
      message: error.message,
      ...(error.details === undefined ? {} : { details: error.details }),
    };
  }
  if (error instanceof Error) return { code: "internal_error", message: error.message };
  return { code: "internal_error", message: String(error) };
}

function aborted(error: unknown, signal: AbortSignal | undefined): boolean {
  return signal?.aborted === true || (error instanceof Error && error.name === "AbortError");
}

async function withCommandTimeout<T>(
  args: CommandArguments,
  parent: AbortSignal | undefined,
  operation: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const timeoutMs = timeoutOption(args);
  const deadline = commandDeadline(parent, timeoutMs);
  try {
    deadline.signal.throwIfAborted();
    return await operation(deadline.signal);
  } catch (error) {
    if (parent?.aborted === true) throw parent.reason;
    if (deadline.timedOut()) {
      throw new MarimoExportError("timeout", `The command stopped waiting after ${timeoutMs}ms.`, {
        cause: error,
      });
    }
    throw error;
  } finally {
    deadline.dispose();
  }
}

function commandDeadline(parent: AbortSignal | undefined, timeoutMs: number) {
  const controller = new AbortController();
  let timeout = false;
  const abort = () => controller.abort(parent?.reason);
  if (parent?.aborted === true) abort();
  else parent?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    timeout = true;
    controller.abort(new DOMException("Command timed out.", "TimeoutError"));
  }, timeoutMs);
  return {
    signal: controller.signal,
    timedOut: () => timeout,
    dispose() {
      clearTimeout(timer);
      parent?.removeEventListener("abort", abort);
    },
  };
}

function defaultIO(): CliIO {
  return {
    stdout: (text) => process.stdout.write(text),
    stderr: (text) => process.stderr.write(text),
  };
}

function helpText(): string {
  return `marimo-export publishes and reads portable notebook projections.

Usage:
  marimo-export publish --server URL (--notebook PATH | --session ID) --plan FILE --out DIR [--record FILE] [--concurrency N] [--timeout-ms MS] [--json]
  marimo-export build --server URL --notebook PATH --plan FILE [--record FILE] [--timeout-ms MS] [--json]
  marimo-export pull BUILD|- --out DIR [--server URL] [--concurrency N] [--timeout-ms MS] [--json]
  marimo-export inspect SOURCE [--scenario ID] [--offset N] [--limit N] [--ref FILE] [--timeout-ms MS] [--json]
  marimo-export read SOURCE SCENARIO OUTPUT [--format NAME] [--max-bytes N] [--out FILE] [--ref FILE] [--timeout-ms MS] [--json]
  marimo-export verify SOURCE [--ref FILE] [--concurrency N] [--timeout-ms MS] [--json]
  marimo-export describe --server URL (--notebook PATH | --session ID) [--timeout-ms MS] [--json]

SOURCE is a publication directory or an HTTP URL. Pass - where a plan or build record is read from stdin.
Build writes a reopenable marimo-export.build.v1 record, so build output can be piped to pull -.

Defaults:
  --concurrency 8 (maximum 64)
  --limit 50 (maximum 500)
  --max-bytes 1000000
  --timeout-ms 300000

Authentication:
  MARIMO_TOKEN
  MARIMO_SERVER_TOKEN

Authenticated pull requires --server URL to match the build record.
`;
}
