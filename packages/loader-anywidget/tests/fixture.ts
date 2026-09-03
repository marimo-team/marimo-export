import { loadAnyWidget } from "../src/index.js";
import type { AnyWidgetStateShape, LoadedAnyWidget } from "../src/index.js";
import type { ModelState, ModelValue } from "../src/runtime/model.js";

const encoder = new TextEncoder();

export const loadPayload = <
  State extends AnyWidgetStateShape<State> = ModelState,
  Exports extends object | undefined = object | undefined,
>(
  payload: ModelValue,
  signal?: AbortSignal,
): Promise<LoadedAnyWidget<State, Exports>> =>
  loadAnyWidget<State, Exports>(encoder.encode(JSON.stringify(payload)), signal);

export function moduleUrl(source: string): string {
  return `data:text/javascript,${encodeURIComponent(source)}`;
}

export function base64ModuleUrl(source: string, marker = "base64"): string {
  return `data:text/javascript;${marker},${btoa(source)}`;
}

export function notification(options: {
  readonly id: string;
  readonly state: ModelState;
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
  readonly modelNotifications: readonly ModelValue[];
}) {
  return {
    schema: "marimo-export.anywidget.v1",
    rootModelId: options.rootModelId ?? "model-0",
    files: options.files ?? {},
    modelNotifications: options.modelNotifications,
  };
}
