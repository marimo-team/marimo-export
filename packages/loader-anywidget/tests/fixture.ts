import { encode } from "@msgpack/msgpack";
import { openPublication } from "@marimo-team/marimo-export";
import type { FormatLoader, PublishedFormat } from "@marimo-team/marimo-export";

const encoder = new TextEncoder();

export async function outputFor(
  payload: unknown,
  options: {
    readonly formatId?: string;
    readonly mediaType?: string;
    readonly loaders?: readonly FormatLoader[];
  } = {},
): Promise<PublishedFormat> {
  const data = encoder.encode(JSON.stringify(payload));
  const formatId = options.formatId ?? "anywidget.v1";
  const mediaType = options.mediaType ?? "application/vnd.marimo-export.anywidget+json";
  const envelope = encode({
    data,
    media_type: mediaType,
    filename: null,
    metadata: { format_id: formatId, metadata_json: encoder.encode("{}") },
  });
  const sha256 = await digest(envelope);
  const key = "C_anywidget/return.bin";
  const index = {
    schema: "marimo-export.publication.v1",
    asset_codec: "marimo.blob-asset.msgpack.v1",
    notebook: { filename: "widget.py", document_sha256: "a".repeat(64) },
    producer: { marimo: "0.24.0", marimo_export: "0.0.0" },
    variants: {
      current: {
        controls: {},
        outputs: {
          widget: {
            formats: {
              anywidget: {
                format_id: formatId,
                media_type: mediaType,
                metadata: {},
                asset: { key, sha256, size: envelope.byteLength },
              },
            },
          },
        },
      },
    },
  };
  const fetch: typeof globalThis.fetch = async (input) => {
    const url = input instanceof Request ? input.url : input.toString();
    if (url.endsWith("/index.json")) return new Response(JSON.stringify(index));
    if (url.endsWith(`/cache/${key}`)) return new Response(new Uint8Array(envelope));
    return new Response(null, { status: 404 });
  };
  const publication = await openPublication("https://example.test/export/", {
    fetch,
    ...(options.loaders === undefined ? {} : { loaders: options.loaders }),
  });
  return publication.variant("current").output("widget").format("anywidget");
}

async function digest(bytes: Uint8Array): Promise<string> {
  const value = await crypto.subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>);
  return [...new Uint8Array(value)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
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
