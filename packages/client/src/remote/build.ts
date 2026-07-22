import type { ExportRef } from "../types.js";
import type { RemoteBuildReceipt, RemoteBuildResult } from "./client.js";

export interface RemoteBuild {
  readonly ref: ExportRef;
  readonly receipt: RemoteBuildReceipt;
}

export function createRemoteBuild(result: RemoteBuildResult): RemoteBuild {
  return Object.freeze({
    ref: Object.freeze({ ...result.ref }),
    receipt: Object.freeze({ ...result.receipt }),
  });
}
