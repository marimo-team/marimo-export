import { randomUUID } from "node:crypto";
import { lstat, mkdir, realpath, rename, rm, writeFile } from "node:fs/promises";
import { basename, dirname, isAbsolute, relative, resolve, sep } from "node:path";

import { verifyBytes } from "../hash.js";
import { openExport, snapshotExport } from "../reader.js";
import type { ExportRef, ExportSource, PayloadRef } from "../types.js";
import { MarimoExportError } from "../types.js";
import type { Remote } from "../remote/session.js";
import { matchesRegularFile } from "./safe-file.js";
import { exportPath } from "./source.js";

const CLEANUP_TIMEOUT_MS = 10_000;

export interface PullExportOptions {
  readonly source: ExportSource;
  readonly into: string;
  readonly ref?: ExportRef;
  readonly concurrency?: number;
  readonly signal?: AbortSignal;
}

export interface PullRemoteOptions {
  readonly into: string;
  readonly concurrency?: number;
  readonly signal?: AbortSignal;
}

export interface PullExportReceipt {
  readonly files: number;
  readonly downloaded: number;
  readonly skipped: number;
  readonly bytes: number;
}

export interface VerifyExportOptions {
  readonly source: ExportSource;
  readonly ref?: ExportRef;
  readonly concurrency?: number;
  readonly signal?: AbortSignal;
}

export interface ExportVerificationFailure {
  readonly key: string;
  readonly message: string;
}

export interface ExportVerification {
  readonly ok: boolean;
  readonly files: number;
  readonly bytes: number;
  readonly failures: readonly ExportVerificationFailure[];
}

export async function preflightExportDestination(into: string): Promise<void> {
  await prepareExportDestination(into);
}

async function prepareExportDestination(into: string): Promise<RootAnchor> {
  const root = await anchorRoot(resolve(into));
  const cache = await anchorRoot(exportPath(root.path, "cache"));
  await assertResolvedInside(root, cache.path);
  await probeWritableRoot(root);
  await probeWritableRoot(cache);
  await assertWritableLeaf(exportPath(root.path, "index.json"));
  return root;
}

export async function preflightOutputFile(path: string): Promise<void> {
  const destination = resolve(path);
  const root = await anchorRoot(dirname(destination));
  await assertWritableLeaf(destination);
  await probeWritableRoot(root);
}

export async function preflightPublicationRecord(record: string, into: string): Promise<void> {
  const publication = await anchorRoot(resolve(into));
  const destination = resolve(record);
  const recordRoot = await anchorRoot(dirname(destination));
  const canonicalDestination = resolve(recordRoot.realPath, basename(destination));
  if (isPathInside(publication.realPath, canonicalDestination)) {
    throw new MarimoExportError(
      "invalid_request",
      "--record must be outside the publication directory.",
    );
  }
  await assertWritableLeaf(destination);
  await probeWritableRoot(recordRoot);
}

export async function writeOutputFileAtomic(path: string, bytes: Uint8Array): Promise<void> {
  const destination = resolve(path);
  const root = await anchorRoot(dirname(destination));
  await writeAtomic(root, destination, bytes);
}

export async function pullExport(options: PullExportOptions): Promise<PullExportReceipt> {
  const concurrency = parseConcurrency(options.concurrency);
  const rootPath = resolve(options.into);
  const root = await prepareExportDestination(rootPath);
  return transferExport(options, root, concurrency);
}

async function transferExport(
  options: PullExportOptions,
  root: RootAnchor,
  concurrency: number,
): Promise<PullExportReceipt> {
  const published = await openExport(options.source, {
    ...(options.ref === undefined ? {} : { ref: options.ref }),
    ...(options.signal === undefined ? {} : { signal: options.signal }),
  });
  const snapshot = snapshotExport(published);
  let downloaded = 0;
  let skipped = 0;
  let bytes = 0;

  await parallel(snapshot.payloads, concurrency, options.signal, async (payload, signal) => {
    const destination = exportPath(root.path, `cache/${payload.key}`);
    if (await matchesIfPresent(root, destination, payload, signal)) {
      skipped += 1;
      bytes += payload.size;
      return;
    }
    const value = new Uint8Array(
      await options.source.read(`cache/${payload.key}`, {
        signal,
        maxBytes: payload.size,
      }),
    );
    signal.throwIfAborted();
    await verifyBytes(value, payload, `Payload ${JSON.stringify(payload.key)}`);
    signal.throwIfAborted();
    await writeAtomic(root, destination, value);
    signal.throwIfAborted();
    downloaded += 1;
    bytes += payload.size;
  });

  options.signal?.throwIfAborted();
  await writeAtomic(root, exportPath(root.path, "index.json"), snapshot.indexBytes);
  return Object.freeze({
    files: snapshot.payloads.length,
    downloaded,
    skipped,
    bytes,
  });
}

export async function pullRemote(
  remote: Remote,
  ref: ExportRef,
  options: PullRemoteOptions,
): Promise<PullExportReceipt> {
  const concurrency = parseConcurrency(options.concurrency);
  const root = await prepareExportDestination(options.into);
  const lease = await remote.open(
    ref,
    options.signal === undefined ? {} : { signal: options.signal },
  );
  let result: PullExportReceipt | undefined;
  let failure: unknown;
  try {
    result = await transferExport(
      {
        source: lease.source,
        into: options.into,
        ref,
        ...(options.concurrency === undefined ? {} : { concurrency: options.concurrency }),
        ...(options.signal === undefined ? {} : { signal: options.signal }),
      },
      root,
      concurrency,
    );
  } catch (error) {
    failure = error;
  }
  try {
    await lease.close({ timeoutMs: CLEANUP_TIMEOUT_MS });
  } catch (error) {
    failure ??= error;
  }
  if (failure !== undefined) throw failure;
  return result!;
}

export async function verifyExport(options: VerifyExportOptions): Promise<ExportVerification> {
  const concurrency = parseConcurrency(options.concurrency);
  const published = await openExport(options.source, {
    ...(options.ref === undefined ? {} : { ref: options.ref }),
    ...(options.signal === undefined ? {} : { signal: options.signal }),
  });
  const payloads = snapshotExport(published).payloads;
  const failures: ExportVerificationFailure[] = [];
  let bytes = 0;

  await parallel(payloads, concurrency, options.signal, async (payload, signal) => {
    try {
      const value = new Uint8Array(
        await options.source.read(`cache/${payload.key}`, {
          signal,
          maxBytes: payload.size,
        }),
      );
      signal.throwIfAborted();
      await verifyBytes(value, payload, `Payload ${JSON.stringify(payload.key)}`);
      signal.throwIfAborted();
      bytes += payload.size;
    } catch (error) {
      if (signal.aborted) throw signal.reason;
      failures.push({
        key: payload.key,
        message: error instanceof Error ? error.message : String(error),
      });
    }
  });

  failures.sort((left, right) => left.key.localeCompare(right.key));
  return Object.freeze({
    ok: failures.length === 0,
    files: payloads.length,
    bytes,
    failures: Object.freeze(failures.map((failure) => Object.freeze(failure))),
  });
}

interface RootAnchor {
  readonly path: string;
  readonly realPath: string;
  readonly device: number | bigint;
  readonly inode: number | bigint;
}

async function anchorRoot(path: string): Promise<RootAnchor> {
  try {
    await mkdir(path, { recursive: true });
  } catch (error) {
    throw unsafeDestination(`Failed to create export destination ${path}.`, error);
  }

  const status = await destinationStatus(path, "export destination");
  if (status.isSymbolicLink() || !status.isDirectory()) {
    throw unsafeDestination(`Export destination ${path} must be a directory, not a symlink.`);
  }

  let resolved: string;
  try {
    resolved = await realpath(path);
  } catch (error) {
    throw unsafeDestination(`Failed to resolve export destination ${path}.`, error);
  }
  return Object.freeze({
    path,
    realPath: resolved,
    device: status.dev,
    inode: status.ino,
  });
}

async function probeWritableRoot(root: RootAnchor): Promise<void> {
  await assertRoot(root);
  const probe = resolve(root.path, `.marimo-export-write-${randomUUID()}`);
  try {
    await writeFile(probe, new Uint8Array(), { flag: "wx" });
    const status = await leafStatus(probe);
    if (status === undefined) throw unsafeDestination(`Write probe ${probe} disappeared.`);
    assertRegularFile(probe, status);
    await assertRoot(root);
  } catch (error) {
    if (error instanceof MarimoExportError) throw error;
    throw unsafeDestination(`Export destination ${root.path} is not writable.`, error);
  } finally {
    await rm(probe, { force: true }).catch(() => undefined);
  }
}

async function matchesIfPresent(
  root: RootAnchor,
  path: string,
  ref: PayloadRef,
  signal: AbortSignal,
): Promise<boolean> {
  await prepareParent(root, path);
  const status = await leafStatus(path);
  if (status === undefined) return false;
  assertRegularFile(path, status);
  try {
    return await matchesRegularFile(path, ref, signal);
  } catch (error) {
    if (signal.aborted) throw signal.reason;
    if (isNodeError(error) && error.code === "ENOENT") return false;
    throw error;
  }
}

async function writeAtomic(root: RootAnchor, path: string, bytes: Uint8Array): Promise<void> {
  await prepareParent(root, path);
  await assertWritableLeaf(path);
  const temporary = `${path}.tmp-${process.pid}-${randomUUID()}`;
  try {
    await writeFile(temporary, bytes, { flag: "wx" });
    await prepareParent(root, path);
    const temporaryStatus = await leafStatus(temporary);
    if (temporaryStatus === undefined) {
      throw unsafeDestination(`Temporary export object ${temporary} disappeared before commit.`);
    }
    assertRegularFile(temporary, temporaryStatus);
    await assertWritableLeaf(path);
    await rename(temporary, path);
    const committed = await leafStatus(path);
    if (committed === undefined) {
      throw unsafeDestination(`Export object ${path} disappeared during commit.`);
    }
    assertRegularFile(path, committed);
  } finally {
    await removeTemporary(root, temporary);
  }
}

async function prepareParent(root: RootAnchor, path: string): Promise<void> {
  await assertRoot(root);
  const parent = dirname(path);
  const relativeParent = relative(root.path, parent);
  if (
    isAbsolute(relativeParent) ||
    relativeParent === ".." ||
    relativeParent.startsWith(`..${sep}`)
  ) {
    throw unsafeDestination(`Export object ${path} escapes ${root.path}.`);
  }

  let current = root.path;
  for (const part of relativeParent.split(sep)) {
    if (part.length === 0 || part === ".") continue;
    // oxlint-disable-next-line no-await-in-loop -- each checked directory anchors the next one.
    await assertDirectoryInside(root, current);
    current = resolve(current, part);
    try {
      // oxlint-disable-next-line no-await-in-loop -- parent directories are created in path order.
      await mkdir(current);
    } catch (error) {
      if (!isNodeError(error) || error.code !== "EEXIST") {
        throw unsafeDestination(`Failed to create export directory ${current}.`, error);
      }
    }
    // oxlint-disable-next-line no-await-in-loop -- validate each component before traversal.
    await assertDirectoryInside(root, current);
  }
  await assertDirectoryInside(root, parent);
}

async function assertRoot(root: RootAnchor): Promise<void> {
  const status = await destinationStatus(root.path, "export destination");
  if (
    status.isSymbolicLink() ||
    !status.isDirectory() ||
    status.dev !== root.device ||
    status.ino !== root.inode
  ) {
    throw unsafeDestination(`Export destination ${root.path} changed during transfer.`);
  }
  await assertResolvedInside(root, root.path);
}

async function assertDirectoryInside(root: RootAnchor, path: string): Promise<void> {
  const status = await destinationStatus(path, "export directory");
  if (status.isSymbolicLink() || !status.isDirectory()) {
    throw unsafeDestination(`Export directory ${path} must be a directory, not a symlink.`);
  }
  await assertResolvedInside(root, path);
}

async function assertResolvedInside(root: RootAnchor, path: string): Promise<void> {
  let resolved: string;
  try {
    resolved = await realpath(path);
  } catch (error) {
    throw unsafeDestination(`Failed to resolve export path ${path}.`, error);
  }
  if (resolved !== root.realPath && !resolved.startsWith(`${root.realPath}${sep}`)) {
    throw unsafeDestination(`Export path ${path} resolves outside ${root.path}.`);
  }
}

async function assertWritableLeaf(path: string): Promise<void> {
  const status = await leafStatus(path);
  if (status !== undefined) assertRegularFile(path, status);
}

function assertRegularFile(path: string, status: Awaited<ReturnType<typeof lstat>>): void {
  if (status.isSymbolicLink() || !status.isFile()) {
    throw unsafeDestination(`Export object ${path} must be a regular file, not a symlink.`);
  }
}

async function leafStatus(path: string): Promise<Awaited<ReturnType<typeof lstat>> | undefined> {
  try {
    return await lstat(path);
  } catch (error) {
    if (isNodeError(error) && error.code === "ENOENT") return undefined;
    throw unsafeDestination(`Failed to inspect export object ${path}.`, error);
  }
}

async function destinationStatus(
  path: string,
  description: string,
): Promise<Awaited<ReturnType<typeof lstat>>> {
  try {
    return await lstat(path);
  } catch (error) {
    throw unsafeDestination(`Failed to inspect ${description} ${path}.`, error);
  }
}

async function removeTemporary(root: RootAnchor, path: string): Promise<void> {
  try {
    await prepareParent(root, path);
    const status = await leafStatus(path);
    if (status === undefined) return;
    assertRegularFile(path, status);
    await rm(path);
  } catch {
    // An unsafe or replaced parent is left untouched so cleanup cannot escape the destination.
  }
}

function unsafeDestination(message: string, cause?: unknown): MarimoExportError {
  return new MarimoExportError("invalid_request", message, cause === undefined ? {} : { cause });
}

function isPathInside(root: string, path: string): boolean {
  const within = relative(root, path);
  return (
    within === "" || (!isAbsolute(within) && within !== ".." && !within.startsWith(`..${sep}`))
  );
}

async function parallel<T>(
  values: readonly T[],
  concurrency: number,
  parentSignal: AbortSignal | undefined,
  worker: (value: T, signal: AbortSignal) => Promise<void>,
): Promise<void> {
  const controller = new AbortController();
  const abort = () => controller.abort(parentSignal?.reason);
  if (parentSignal?.aborted === true) abort();
  else parentSignal?.addEventListener("abort", abort, { once: true });
  let cursor = 0;
  let failure: unknown;
  const take = (): T | undefined => {
    if (cursor >= values.length) return undefined;
    const value = values[cursor];
    cursor += 1;
    return value;
  };
  try {
    await Promise.all(
      Array.from({ length: Math.min(concurrency, values.length) }, async () => {
        while (failure === undefined) {
          try {
            controller.signal.throwIfAborted();
            const value = take();
            if (value === undefined) return;
            // oxlint-disable-next-line no-await-in-loop -- each worker consumes one bounded queue.
            await worker(value, controller.signal);
          } catch (error) {
            failure ??= error;
            controller.abort(error);
          }
        }
      }),
    );
  } finally {
    parentSignal?.removeEventListener("abort", abort);
  }
  if (failure !== undefined) throw failure;
}

function parseConcurrency(input: number | undefined): number {
  const value = input ?? 8;
  if (!Number.isSafeInteger(value) || value < 1 || value > 64) {
    throw new MarimoExportError(
      "invalid_request",
      "concurrency must be an integer from 1 through 64.",
    );
  }
  return value;
}

function isNodeError(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && "code" in error;
}
