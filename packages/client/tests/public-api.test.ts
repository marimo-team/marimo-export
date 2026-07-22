import { describe, expect, expectTypeOf, test } from "vite-plus/test";

import * as nodeApi from "../src/node.js";
import * as rootApi from "../src/index.js";
import * as remoteApi from "../src/remote.js";
import type {
  ExportSource,
  ExportOutput,
  HttpSourceOptions,
  MemorySourceInput,
  NotebookExport,
  OpenExportOptions,
  OutputLoaderContext,
} from "../src/index.js";
import type {
  ConnectRemoteOptions,
  ExportPlan,
  Remote,
  RemoteBuild,
  RemoteBuildReceipt,
  RemoteRequestOptions,
} from "../src/remote.js";
import type {
  ExportVerification,
  PullExportOptions,
  PullExportReceipt,
  PullRemoteOptions,
  VerifyExportOptions,
} from "../src/node.js";
import type { ExportRef } from "../src/index.js";

describe("public package entrypoints", () => {
  test("expose the exact runtime API", () => {
    expect(Object.keys(rootApi).sort()).toEqual([
      "MarimoExportError",
      "httpSource",
      "memorySource",
      "openExport",
    ]);
    expect(Object.keys(remoteApi).sort()).toEqual(["connectRemote", "validateExportPlan"]);
    expect(Object.keys(nodeApi).sort()).toEqual([
      "directorySource",
      "pullExport",
      "pullRemote",
      "verifyExport",
    ]);
  });

  test("keeps stable public function signatures", () => {
    expectTypeOf(rootApi.openExport).toEqualTypeOf<
      (source: ExportSource, options?: OpenExportOptions) => Promise<NotebookExport>
    >();
    expectTypeOf(rootApi.httpSource).toEqualTypeOf<
      (root: string | URL, options?: HttpSourceOptions) => ExportSource
    >();
    expectTypeOf(rootApi.memorySource).toEqualTypeOf<(input: MemorySourceInput) => ExportSource>();
    expectTypeOf<ExportOutput["formatName"]>().toEqualTypeOf<string>();
    expectTypeOf<OutputLoaderContext["signal"]>().toEqualTypeOf<AbortSignal | undefined>();

    expectTypeOf(remoteApi.connectRemote).toEqualTypeOf<
      (options: ConnectRemoteOptions) => Promise<Remote>
    >();
    expectTypeOf(remoteApi.validateExportPlan).toEqualTypeOf<(input: unknown) => ExportPlan>();
    expectTypeOf<Remote["build"]>().toEqualTypeOf<
      (plan: ExportPlan, options?: RemoteRequestOptions) => Promise<RemoteBuild>
    >();
    expectTypeOf<RemoteBuild>().toEqualTypeOf<{
      readonly ref: ExportRef;
      readonly receipt: RemoteBuildReceipt;
    }>();

    expectTypeOf(nodeApi.directorySource).toEqualTypeOf<(rootPath: string) => ExportSource>();
    expectTypeOf(nodeApi.pullExport).toEqualTypeOf<
      (options: PullExportOptions) => Promise<PullExportReceipt>
    >();
    expectTypeOf(nodeApi.pullRemote).toEqualTypeOf<
      (remote: Remote, ref: ExportRef, options: PullRemoteOptions) => Promise<PullExportReceipt>
    >();
    expectTypeOf(nodeApi.verifyExport).toEqualTypeOf<
      (options: VerifyExportOptions) => Promise<ExportVerification>
    >();
  });
});
