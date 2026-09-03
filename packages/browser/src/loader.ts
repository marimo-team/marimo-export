import { parseMediaType } from "./media-type.js";
import type {
  AnyOutputLoader,
  BlobAssetLoadInput,
  BlobAssetLoader,
  MediaType,
  MountableValue,
  MountOptions,
  OutputCodec,
  OutputLoader,
  ExportOutput,
  ScalarValue,
} from "./types.js";
import { NotebookExportError } from "./types.js";
import { isBooleanValue, isCallableValue, isRecordValue, isStringValue } from "./value-types.js";

const CODECS = new Set<OutputCodec>([
  "marimo.scalar.v1",
  "marimo.json.v1",
  "marimo.output.v1",
  "marimo.cell.v1",
  "numpy.npy.v1",
  "apache.arrow.file.v1",
  "marimo.blob-asset.msgpack.v1",
]);

export function defineOutputLoader<C extends OutputCodec, T>(
  loader: OutputLoader<C, T>,
): OutputLoader<C, T> {
  if (
    !isRecordValue(loader) ||
    !CODECS.has(loader.codec) ||
    !hasCallableMember(loader, "accepts") ||
    !hasCallableMember(loader, "load")
  ) {
    throw new TypeError("OutputLoader must define a supported codec, accepts, and load.");
  }
  return Object.freeze(loader);
}

export function defineBlobAssetLoader<T>(definition: {
  readonly mediaTypes: string | readonly string[] | ((mediaType: MediaType) => boolean);
  load(input: BlobAssetLoadInput): T | Promise<T>;
}): BlobAssetLoader<T> {
  if (!isRecordValue(definition) || !hasCallableMember(definition, "load")) {
    throw new TypeError("BlobAssetLoader must define mediaTypes and load.");
  }
  let accepts: (mediaType: MediaType) => boolean;
  if (isMediaTypePredicate(definition.mediaTypes)) {
    accepts = definition.mediaTypes;
  } else {
    const values = isMediaTypeString(definition.mediaTypes)
      ? [definition.mediaTypes]
      : definition.mediaTypes;
    if (!Array.isArray(values) || values.length === 0) {
      throw new TypeError("BlobAssetLoader mediaTypes must not be empty.");
    }
    const essences = new Set(values.map((value) => parseMediaType(value).essence));
    accepts = (mediaType) => essences.has(mediaType.essence);
  }
  return defineOutputLoader({
    codec: "marimo.blob-asset.msgpack.v1",
    accepts: (_descriptor, mediaType) => accepts(mediaType),
    load: (input) => definition.load(input),
  });
}

export function resolveOutputLoader(
  output: ExportOutput,
  loaders: readonly AnyOutputLoader[],
): AnyOutputLoader {
  if (!Array.isArray(loaders)) throw new TypeError("loaders must be an array.");
  const matches: AnyOutputLoader[] = [];
  for (const loader of loaders) {
    if (!isRecordValue(loader) || loader.codec !== output.codec) continue;
    if (!hasCallableMember(loader, "accepts") || !hasCallableMember(loader, "load")) {
      throw invalidLoader(output, new TypeError("Loader methods are missing."));
    }
    let accepted: boolean;
    try {
      // SAFETY: The matching codec identifies the descriptor member accepted by this loader.
      accepted = loader.accepts(output.descriptor as never, output.mediaType);
    } catch (error) {
      throw invalidLoader(output, error);
    }
    if (!isBooleanValue(accepted)) {
      throw invalidLoader(output, new TypeError("Loader accepts must return a boolean."));
    }
    if (accepted) matches.push(loader);
  }
  if (matches.length === 0) {
    throw new NotebookExportError(
      "loader_unavailable",
      `No OutputLoader accepts ${output.codec} with ${output.mediaType.essence}.`,
      {
        details: {
          output: output.name,
          codec: output.codec,
          mediaType: output.mediaType.raw,
        },
      },
    );
  }
  if (matches.length > 1) {
    throw new NotebookExportError(
      "loader_ambiguous",
      `Multiple OutputLoaders accept ${output.codec} with ${output.mediaType.essence}.`,
      {
        details: {
          output: output.name,
          codec: output.codec,
          mediaType: output.mediaType.raw,
          matches: matches.length,
        },
      },
    );
  }
  return matches[0]!;
}

export function scalarLoader(): OutputLoader<"marimo.scalar.v1", ScalarValue> {
  return defineOutputLoader({
    codec: "marimo.scalar.v1",
    accepts: () => true,
    load: ({ payload }) => payload,
  });
}

export function imageLoader(): BlobAssetLoader<MountableValue> {
  return defineBlobAssetLoader({
    mediaTypes: (mediaType) => mediaType.type === "image",
    load: ({ payload }) =>
      Object.freeze({
        async mount(element: HTMLElement, options: MountOptions = {}) {
          options.signal?.throwIfAborted();
          const blob = new Blob([payload.data.slice()], { type: payload.mediaType.raw });
          const url = URL.createObjectURL(blob);
          const image = document.createElement("img");
          image.src = url;
          image.alt = payload.filename ?? "";
          image.decoding = "async";
          let disposed = false;
          const dispose = () => {
            if (disposed) return;
            disposed = true;
            options.signal?.removeEventListener("abort", dispose);
            image.remove();
            URL.revokeObjectURL(url);
          };
          options.signal?.addEventListener("abort", dispose, { once: true });
          if (options.signal?.aborted) {
            dispose();
            options.signal.throwIfAborted();
          }
          try {
            element.append(image);
            return Object.freeze({ dispose });
          } catch (error) {
            dispose();
            throw error;
          }
        },
      }),
  });
}

function invalidLoader(output: ExportOutput, cause: unknown): NotebookExportError {
  return new NotebookExportError("loader_invalid", "OutputLoader validation failed.", {
    cause,
    details: {
      output: output.name,
      codec: output.codec,
      mediaType: output.mediaType.raw,
    },
  });
}

function isMediaTypePredicate(
  value: string | readonly string[] | ((mediaType: MediaType) => boolean),
): value is (mediaType: MediaType) => boolean {
  return isCallableValue(value);
}

function isMediaTypeString(value: string | readonly string[]): value is string {
  return isStringValue(value);
}

function hasCallableMember<Value extends object>(value: Value, name: PropertyKey): boolean {
  let owner: object | null = value;
  while (owner !== null) {
    const descriptor = Object.getOwnPropertyDescriptor(owner, name);
    if (descriptor !== undefined) {
      if ("value" in descriptor) return isCallableValue(descriptor.value);
      return isCallableValue(descriptor.get?.call(value));
    }
    owner = Object.getPrototypeOf(owner);
  }
  return false;
}
