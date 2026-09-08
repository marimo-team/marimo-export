import { isAbortError } from "./abort.js";
import type {
  AnyOutputLoader,
  ExportState,
  LoadOptions,
  OutputCodec,
  OutputLoader,
} from "./types.js";
import { isNotebookExportError, NotebookExportError } from "./types.js";
import { isCallableValue } from "./value-types.js";

type LoadedOutputs<Loaders extends Readonly<Record<string, AnyOutputLoader>>> = {
  readonly [Name in keyof Loaders]: Awaited<ReturnType<Loaders[Name]["load"]>>;
};

/** Load a state's selected outputs and settle sibling requests after a failure. */
export async function loadOutputs<const Loaders extends Readonly<Record<string, AnyOutputLoader>>>(
  state: ExportState,
  loaders: Loaders,
  options: LoadOptions = {},
): Promise<LoadedOutputs<Loaders>> {
  const controller = new AbortController();
  const abort = () => controller.abort(options.signal?.reason);
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) abort();
  let failure: { readonly cause: unknown } | undefined;
  try {
    requireActive(controller.signal);
    const outputs = Object.entries(loaders).map(([name, loader]) => ({
      name,
      output: state.output(name),
      loader,
    }));
    const results = await Promise.allSettled(
      outputs.map(async ({ name, output, loader }) => {
        try {
          // SAFETY: ExportOutput.load validates the selected codec and loader together.
          const value = await output.load(loader as OutputLoader<OutputCodec, unknown>, {
            ...options,
            signal: controller.signal,
          });
          return [name, value] as const;
        } catch (cause) {
          if (failure === undefined) {
            failure = { cause };
            controller.abort(cause);
          }
          throw cause;
        }
      }),
    );
    if (failure !== undefined) {
      const primary = failure.cause;
      const others = results.flatMap((result) =>
        result.status === "rejected" && result.reason !== primary && !aborted(result.reason)
          ? [result.reason]
          : [],
      );
      if (others.length > 0) {
        throw new AggregateError([primary, ...new Set(others)], "Export output loading failed.");
      }
      throw primary;
    }
    requireActive(controller.signal);
    const entries = results.flatMap((result) =>
      result.status === "fulfilled" ? [result.value] : [],
    );
    const loaded = Object.fromEntries(entries);
    if (isCallableValue(loaded.then)) {
      throw new NotebookExportError(
        "decode_failed",
        'Output "then" is callable. Load it individually with ExportOutput.load().',
        { details: { output: "then" } },
      );
    }
    // SAFETY: Each requested name is paired with the result of its selected loader.
    return Object.freeze(loaded) as LoadedOutputs<Loaders>;
  } finally {
    options.signal?.removeEventListener("abort", abort);
  }
}

function requireActive(signal: AbortSignal): void {
  if (signal.aborted) {
    throw new NotebookExportError("abort", "Export output loading was aborted.", {
      cause: signal.reason,
    });
  }
}

function aborted(cause: unknown): boolean {
  return isAbortError(cause) || (isNotebookExportError(cause) && cause.code === "abort");
}
