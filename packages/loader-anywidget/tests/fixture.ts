import { memorySource, openExport } from "@marimo-team/marimo-export";
import type { ExportOutput } from "@marimo-team/marimo-export";

const encoder = new TextEncoder();

export async function outputFor(
  payload: unknown,
  options: {
    readonly formatId?: string;
    readonly mediaType?: string;
  } = {},
): Promise<ExportOutput> {
  const payloadBytes = encoder.encode(JSON.stringify(payload));
  const payloadSha = await sha256(payloadBytes);
  const key = `marimo-export/payloads/sha256/${payloadSha}`;
  const index = {
    schema: "marimo-export.index.v1",
    notebook: { name: "widget.py", source_sha256: "a".repeat(64) },
    plan_sha256: "b".repeat(64),
    producer: { marimo_version: "0.23.14", marimo_export_version: "0.0.0" },
    scenarios: [
      {
        id: "baseline",
        inputs: {},
        outputs: {
          widget: {
            interactive: {
              format_id: options.formatId ?? "anywidget.v1",
              media_type: options.mediaType ?? "application/vnd.marimo-export.anywidget+json",
              metadata: {},
              payload: { key, sha256: payloadSha, size: payloadBytes.byteLength },
            },
          },
        },
      },
    ],
  };
  const indexBytes = encoder.encode(JSON.stringify(index));
  const published = await openExport(
    memorySource({ "index.json": indexBytes, [`cache/${key}`]: payloadBytes }),
  );
  return published.scenario("baseline").output("widget", "interactive");
}

export function notification(options: {
  readonly id: string;
  readonly state: Record<string, unknown>;
  readonly moduleUrl?: string;
  readonly bufferPaths?: readonly (readonly (string | number)[])[];
  readonly buffers?: readonly string[];
}) {
  return {
    op: "model-lifecycle",
    model_id: options.id,
    message: {
      method: "open",
      state: options.state,
      buffer_paths: options.bufferPaths ?? [],
      buffers: options.buffers ?? [],
      esm_spec:
        options.moduleUrl === undefined
          ? null
          : { url: options.moduleUrl, hash: `hash-${options.id}` },
    },
  };
}

export function payload(options: {
  readonly rootModelId?: string;
  readonly files?: Record<string, string>;
  readonly modelNotifications: readonly unknown[];
}) {
  return {
    schema: "marimo-export.anywidget.v1",
    rootModelId: options.rootModelId ?? "model-0",
    files: options.files ?? {},
    modelNotifications: options.modelNotifications,
  };
}

export function moduleUrl(source: string): string {
  return `data:text/javascript,${encodeURIComponent(source)}`;
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new Uint8Array(bytes));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
