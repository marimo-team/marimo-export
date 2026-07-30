import { encode } from "@msgpack/msgpack";
import { openExport } from "@marimo-team/marimo-export";
import type { ExportOutput } from "@marimo-team/marimo-export";

const encoder = new TextEncoder();

export async function outputFor(
  payload: unknown,
  options: {
    readonly mediaType?: string;
  } = {},
): Promise<ExportOutput> {
  const data = encoder.encode(JSON.stringify(payload));
  const mediaType = options.mediaType ?? "application/vnd.marimo-export.anywidget.v1+json";
  const envelope = encode({
    data,
    media_type: mediaType,
    filename: null,
    metadata: {},
  });
  const sha256 = await digest(envelope);
  const inputs = {};
  const fingerprint = await digest(encoder.encode("{}"));
  const index = {
    inputs: [],
    notebook: { document_sha256: "a".repeat(64), filename: "widget.py" },
    outputs: ["widget"],
    producer: { marimo: "0.24.0", marimo_export: "0.0.0" },
    schema: "marimo-export.export.v1",
    states: {
      current: {
        fingerprint,
        inputs,
        outputs: {
          widget: {
            asset: { sha256, size: envelope.byteLength },
            codec: "marimo.blob-asset.msgpack.v1",
            filename: null,
            media_type: mediaType,
            metadata: {},
            provenance: {
              cache_key: "cell_cache/O_widget.json",
              python_type: "marimo._save.cache.BlobAsset",
              return_reference: "cell_cache/O_widget/return.bin",
            },
          },
        },
      },
    },
  };
  const fetch: typeof globalThis.fetch = async (input) => {
    const url = input instanceof Request ? input.url : input.toString();
    if (url.endsWith("/index.json")) return new Response(canonicalJson(index));
    if (url.endsWith(`/assets/${sha256}.bin`)) {
      return new Response(new Uint8Array(envelope));
    }
    return new Response(null, { status: 404 });
  };
  const notebookExport = await openExport("https://example.test/export/", { fetch });
  return notebookExport.state("current").output("widget");
}

async function digest(bytes: Uint8Array): Promise<string> {
  const value = await crypto.subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>);
  return [...new Uint8Array(value)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key])}`)
    .join(",")}}`;
}

export function moduleUrl(source: string): string {
  return `data:text/javascript,${encodeURIComponent(source)}`;
}

export function base64ModuleUrl(source: string, marker = "base64"): string {
  return `data:text/javascript;${marker},${btoa(source)}`;
}

export function notification(options: {
  readonly id: string;
  readonly state: Record<string, unknown>;
  readonly moduleUrl?: string;
  readonly moduleHash?: string;
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
          : { url: options.moduleUrl, hash: options.moduleHash ?? `hash-${options.id}` },
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
