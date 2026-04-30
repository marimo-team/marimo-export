import {
  defineLoader,
  type ArtifactLoader,
  type ArtifactLoaderContext,
  type ArtifactRecord,
  type BlobRef,
  type JsonValue,
} from "@marimo-team/export-reader";

import type { AnyWidgetDescriptor, AnyWidgetState } from "#anywidget/manifest";
import { createStandaloneWidget } from "#anywidget/runtime/export-runtime";
import { restoreBufferBytes } from "#anywidget/runtime/buffers";
import type {
  CreateStandaloneWidgetOptions,
  StandaloneWidget,
  WidgetModuleNamespace,
} from "#anywidget/runtime/types";

export { createStandaloneWidget } from "#anywidget/runtime/export-runtime";
export { restoreBufferBytes, restoreBuffers } from "#anywidget/runtime/buffers";
export { createWidgetStore } from "#anywidget/runtime/widget-store";
export type {
  AnyModel,
  ClientCommand,
  ClientHost,
  CreateStandaloneWidgetOptions,
  StandaloneWidget,
  WidgetDefinition,
  WidgetModuleNamespace,
} from "#anywidget/runtime/types";
export type {
  CreateWidgetStoreOptions,
  WidgetStore,
  WidgetStoreSelectOptions,
  WidgetStoreSource,
  WidgetStoreWriteOptions,
} from "#anywidget/runtime/widget-store";
export type {
  AnyWidgetAssetRefs,
  AnyWidgetBufferRef,
  AnyWidgetDescriptor,
  AnyWidgetState,
} from "#anywidget/manifest";

export const anywidgetFormat = "anywidget.bundle.v1";

export interface LoadedAnyWidget<T extends AnyWidgetState = AnyWidgetState> {
  artifact: ArtifactRecord;
  descriptor: AnyWidgetDescriptor;
  initialState: T;
  sourceModuleUrl: string;
  moduleUrl: string;
  styleUrl: string | null;
  createWidget(input?: CreateStandaloneWidgetOptions<T>["input"]): Promise<StandaloneWidget<T>>;
  mount(
    el: HTMLElement,
    input?: CreateStandaloneWidgetOptions<T>["input"],
  ): Promise<{
    widget: StandaloneWidget<T>;
    unmount(): Promise<void>;
  }>;
  dispose(): void;
}

export function anywidgetLoader(): ArtifactLoader<LoadedAnyWidget> {
  return defineLoader({
    formats: anywidgetFormat,
    async load(context: ArtifactLoaderContext) {
      return loadAnyWidget(context);
    },
  });
}

async function loadAnyWidget<T extends AnyWidgetState>(
  context: ArtifactLoaderContext,
): Promise<LoadedAnyWidget<T>> {
  const descriptor = await context.json<AnyWidgetDescriptor>("descriptor");
  const initialState = await loadInitialState<T>(context, descriptor);
  const inlineCssText = context.artifact.data.files.style ? await context.text("style") : null;
  const sourceModuleUrl = context.url("module");
  const moduleUrl = URL.createObjectURL(
    new Blob([await context.text("module")], {
      type: "text/javascript",
    }),
  );
  const styleUrl = context.artifact.data.files.style ? context.url("style") : null;
  const createWidget = async (
    input?: CreateStandaloneWidgetOptions<T>["input"],
  ): Promise<StandaloneWidget<T>> => {
    const widgetModule = await importRuntimeModule<T>(moduleUrl);
    const options: CreateStandaloneWidgetOptions<T> = {
      widgetModule,
      anywidgetId: descriptor.anywidget_id,
      initialState,
      inlineCssText,
    };
    if (input !== undefined) {
      options.input = input;
    }
    return createStandaloneWidget(options);
  };

  return {
    artifact: context.artifact,
    descriptor,
    initialState,
    sourceModuleUrl,
    moduleUrl,
    styleUrl,
    createWidget,
    async mount(el, input) {
      const widget = await createWidget(input);
      const mount = await widget.mount(el);
      return {
        widget,
        async unmount() {
          await mount.unmount();
          await widget.destroy();
        },
      };
    },
    dispose() {
      URL.revokeObjectURL(moduleUrl);
    },
  };
}

const importModule = new Function("moduleUrl", "return import(moduleUrl)") as (
  moduleUrl: string,
) => Promise<unknown>;

async function importRuntimeModule<T extends AnyWidgetState>(
  moduleUrl: string,
): Promise<WidgetModuleNamespace<T>> {
  return (await importModule(moduleUrl)) as WidgetModuleNamespace<T>;
}

async function loadInitialState<T extends AnyWidgetState>(
  context: ArtifactLoaderContext,
  descriptor: AnyWidgetDescriptor,
): Promise<T> {
  const state: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(descriptor.state)) {
    state[key] =
      isBlobRef(value) && context.artifact.data.files[`state.${key}`]
        ? await context.json<JsonValue>(`state.${key}`)
        : value;
  }

  const bufferPaths = descriptor.buffers.map((buffer) => buffer.path);
  const buffers = await Promise.all(
    descriptor.buffers.map((_buffer, index) => context.bytes(`buffer_${index}`)),
  );

  return restoreBufferBytes(state as T, bufferPaths, buffers);
}

function isBlobRef(value: unknown): value is BlobRef {
  return (
    typeof value === "object" &&
    value !== null &&
    "href" in value &&
    "sha256" in value &&
    "size" in value
  );
}
