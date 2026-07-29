import { openPublication } from "@marimo-team/marimo-export";
import { arrowTableLoader } from "@marimo-team/marimo-export/loader/arrow";

const status = document.querySelector<HTMLElement>("#arrow-only-status");
if (status === null) throw new Error("#arrow-only-status is missing.");

const base = new URLSearchParams(location.search).get("publication") ?? "./publication/";

try {
  const publication = await openPublication(base);
  const state = publication.states()[0];
  if (state === undefined) throw new Error("Publication has no states.");
  const table = await state.output("prices_arrow").load(arrowTableLoader());
  status.textContent = `${table.numRows} rows × ${table.numCols} columns`;
  document.body.dataset.status = "ready";
} catch (error) {
  status.textContent = error instanceof Error ? error.message : String(error);
  document.body.dataset.status = "error";
}
