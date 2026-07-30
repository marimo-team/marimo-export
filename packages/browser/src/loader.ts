import { parseMediaType } from "./media-type.js";
import type {
  AnyOutputLoader,
  BlobAssetLoadInput,
  BlobAssetLoader,
  MediaType,
  MountableValue,
  OutputCodec,
  OutputLoader,
  ExportOutput,
  ScalarValue,
} from "./types.js";
import { NotebookExportError } from "./types.js";

const CODECS = new Set<OutputCodec>([
  "marimo.scalar.v1",
  "numpy.npy.v1",
  "apache.arrow.file.v1",
  "marimo.blob-asset.msgpack.v1",
]);

export function defineOutputLoader<C extends OutputCodec, T>(
  loader: OutputLoader<C, T>,
): OutputLoader<C, T> {
  if (
    loader === null ||
    typeof loader !== "object" ||
    !CODECS.has(loader.codec) ||
    typeof loader.accepts !== "function" ||
    typeof loader.load !== "function"
  ) {
    throw new TypeError("OutputLoader must define a supported codec, accepts, and load.");
  }
  return Object.freeze(loader);
}

export function defineBlobAssetLoader<T>(definition: {
  readonly mediaTypes: string | readonly string[] | ((mediaType: MediaType) => boolean);
  load(input: BlobAssetLoadInput): T | Promise<T>;
}): BlobAssetLoader<T> {
  if (
    definition === null ||
    typeof definition !== "object" ||
    typeof definition.load !== "function"
  ) {
    throw new TypeError("BlobAssetLoader must define mediaTypes and load.");
  }
  let accepts: (mediaType: MediaType) => boolean;
  if (typeof definition.mediaTypes === "function") {
    accepts = definition.mediaTypes;
  } else {
    const values =
      typeof definition.mediaTypes === "string" ? [definition.mediaTypes] : definition.mediaTypes;
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
    if (loader === null || typeof loader !== "object" || loader.codec !== output.codec) continue;
    if (typeof loader.accepts !== "function" || typeof loader.load !== "function") {
      throw invalidLoader(output, new TypeError("Loader methods are missing."));
    }
    let accepted: unknown;
    try {
      accepted = loader.accepts(output.descriptor as never, output.mediaType);
    } catch (error) {
      throw invalidLoader(output, error);
    }
    if (typeof accepted !== "boolean") {
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
        async mount(element: HTMLElement, options: { readonly signal?: AbortSignal } = {}) {
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
