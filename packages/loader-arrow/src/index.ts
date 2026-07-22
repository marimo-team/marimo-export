import { tableFromIPC } from "@uwdata/flechette";
import type { ExtractionOptions, Table, TypeMap } from "@uwdata/flechette";
import type { OutputLoader } from "@marimo-team/marimo-export";

const FORMAT_ID = "dataframe.arrow.v1";

export type ArrowOptions = ExtractionOptions;

/** Decode an Arrow IPC projection into a Flechette table. */
export function arrow<T extends TypeMap = TypeMap>(
  options: ArrowOptions = {},
): OutputLoader<Table<T>> {
  const settings = { ...options };
  return {
    formatId: FORMAT_ID,
    async load(output) {
      return tableFromIPC(await output.bytes(), settings) as Table<T>;
    },
  };
}
