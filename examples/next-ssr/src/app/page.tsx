import { anywidget } from "@marimo-team/marimo-export-anywidget";
import { openExport } from "@marimo-team/marimo-export";
import { directorySource } from "@marimo-team/marimo-export/node";

import { WidgetClient } from "./widget-client";

export const dynamic = "force-dynamic";

interface DashboardState extends Record<string, unknown> {
  readonly accent: string;
  readonly child: string;
  readonly title: string;
}

export default async function Page() {
  const root = process.env.MARIMO_EXPORT_DIR;
  if (root === undefined) {
    return (
      <main data-export-ssr="missing-directory">
        <p className="eyebrow">Next.js Server Component</p>
        <h1>Pythonless AnyWidget</h1>
        <p>
          Set <code>MARIMO_EXPORT_DIR</code> to the widgets publication directory.
        </p>
      </main>
    );
  }

  const published = await openExport(directorySource(root));
  const scenario = published.scenario("baseline");
  const widget = await scenario
    .output("wrapped_dashboard", "anywidget")
    .load(anywidget<DashboardState>());
  const initialState = {
    accent: widget.initialState.accent,
    child: widget.initialState.child,
    title: widget.initialState.title,
  };

  return (
    <main data-export-ssr="ready" data-scenario={scenario.id}>
      <p className="eyebrow">Next.js Server Component and Client Component</p>
      <h1>Pythonless AnyWidget</h1>
      <p className="lede">
        The server decoded the published model graph from the filesystem. The browser fetches the
        same publication from <code>/export</code> and mounts its frontend module.
      </p>

      <section className="panel" aria-labelledby="ssr-heading">
        <p className="step">1 · Server-rendered state</p>
        <h2 id="ssr-heading">Inert model snapshot</h2>
        <pre data-ssr-initial-state>{JSON.stringify(initialState, null, 2)}</pre>
      </section>

      <WidgetClient scenarioId={scenario.id} />
    </main>
  );
}
