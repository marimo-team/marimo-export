import {
  defineLoader,
  type FormatLoader,
  type FormatLoaderContext,
  type FormatRecord,
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

export { createWidgetStore } from "#anywidget/runtime/widget-store";
export type {
  CreateWidgetStoreOptions,
  WidgetStore,
  WidgetStoreSelectOptions,
  WidgetStoreWriteOptions,
} from "#anywidget/runtime/widget-store";
export type {
  AnyWidgetAssetRefs,
  AnyWidgetBufferRef,
  AnyWidgetDescriptor,
  AnyWidgetState,
} from "#anywidget/manifest";

export const anywidgetFormat = "anywidget.bundle.v1";

export type AnyWidgetCommand = (
  msg: unknown,
  options: { buffers: Uint8Array[]; signal?: AbortSignal },
) => Promise<[unknown, Uint8Array[]]> | [unknown, Uint8Array[]];

export interface AnyWidgetInput<T extends AnyWidgetState = AnyWidgetState> {
  state?: Partial<T>;
  commands?: Record<string, AnyWidgetCommand>;
}

export interface MountedAnyWidget<T extends AnyWidgetState = AnyWidgetState> {
  widget: StandaloneWidget<T>;
  unmount(): Promise<void>;
}

export interface LoadedAnyWidget<T extends AnyWidgetState = AnyWidgetState> {
  record: FormatRecord;
  descriptor: AnyWidgetDescriptor;
  initialState: T;
  createWidget(input?: AnyWidgetInput<T>): Promise<StandaloneWidget<T>>;
  mount(el: HTMLElement, input?: AnyWidgetInput<T>): Promise<MountedAnyWidget<T>>;
  dispose(): void;
}

export function anywidgetLoader<T extends AnyWidgetState = AnyWidgetState>(): FormatLoader<
  LoadedAnyWidget<T>
> {
  return defineLoader({
    formatId: anywidgetFormat,
    async load(context: FormatLoaderContext) {
      return loadAnyWidget<T>(context);
    },
  });
}

async function loadAnyWidget<T extends AnyWidgetState>(
  context: FormatLoaderContext,
): Promise<LoadedAnyWidget<T>> {
  const descriptor = await context.file("descriptor").json<AnyWidgetDescriptor>();
  const initialState = await loadInitialState<T>(context, descriptor);
  const inlineCssText = context.record.data.files.style
    ? await context.file("style").text()
    : null;
  const objectUrls = new Set<string>();

  const createWidgetWithCleanup = async (
    input?: AnyWidgetInput<T>,
  ): Promise<{ widget: StandaloneWidget<T>; dispose(): void }> => {
    const moduleUrl = URL.createObjectURL(
      new Blob([await context.file("module").text()], {
        type: "text/javascript",
      }),
    );
    objectUrls.add(moduleUrl);
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
    return {
      widget: await createStandaloneWidget(options),
      dispose() {
        URL.revokeObjectURL(moduleUrl);
        objectUrls.delete(moduleUrl);
      },
    };
  };

  return {
    record: context.record,
    descriptor,
    initialState,
    async createWidget(input) {
      return (await createWidgetWithCleanup(input)).widget;
    },
    async mount(el, input) {
      const created = await createWidgetWithCleanup(input);
      const widget = created.widget;
      const mount = await widget.mount(el);
      return {
        widget,
        async unmount() {
          try {
            await mount.unmount();
            await widget.destroy();
          } finally {
            created.dispose();
          }
        },
      };
    },
    dispose() {
      for (const url of objectUrls) {
        URL.revokeObjectURL(url);
      }
      objectUrls.clear();
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
  context: FormatLoaderContext,
  descriptor: AnyWidgetDescriptor,
): Promise<T> {
  const state: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(descriptor.state)) {
    state[key] =
      isBlobRef(value) && context.record.data.files[`state.${key}`]
        ? await context.file(`state.${key}`).json<JsonValue>()
        : value;
  }

  const bufferPaths = descriptor.buffers.map((buffer) => buffer.path);
  const buffers = await Promise.all(
    descriptor.buffers.map((_buffer, index) => context.file(`buffer_${index}`).bytes()),
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
