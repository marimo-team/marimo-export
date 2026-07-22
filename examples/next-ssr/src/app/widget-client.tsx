"use client";

import { anywidget, type MountedAnyWidget } from "@marimo-team/marimo-export-anywidget";
import { httpSource, openExport } from "@marimo-team/marimo-export";
import { useEffect, useRef, useState } from "react";

interface DashboardState extends Record<string, unknown> {
  accent: string;
  child: string;
  title: string;
}

interface DashboardExports {
  rename(title: string): void;
}

type Mount = MountedAnyWidget<DashboardState, DashboardExports>;
type Status = "loading" | "ready" | "disposed" | "error";

export function WidgetClient({ scenarioId }: { readonly scenarioId: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mountRef = useRef<Mount | undefined>(undefined);
  const [status, setStatus] = useState<Status>("loading");
  const [title, setTitle] = useState("Waiting for browser mount");
  const [error, setError] = useState<string>();

  useEffect(() => {
    const controller = new AbortController();
    let current: Mount | undefined;

    async function mountWidget() {
      const element = hostRef.current;
      if (element === null) throw new Error("The AnyWidget mount element is missing.");

      const published = await openExport(httpSource("/export/"), {
        signal: controller.signal,
      });
      const loaded = await published
        .scenario(scenarioId)
        .output("wrapped_dashboard", "anywidget")
        .load(anywidget<DashboardState, DashboardExports>(), { signal: controller.signal });
      current = await loaded.mount(element, { signal: controller.signal });
      mountRef.current = current;
      setTitle(current.model.get("title"));
      setStatus("ready");
    }

    void mountWidget().catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : String(reason));
      setStatus("error");
    });

    return () => {
      controller.abort();
      mountRef.current = undefined;
      if (current !== undefined) void current.dispose().catch(() => undefined);
    };
  }, [scenarioId]);

  function renameDashboard() {
    const mounted = mountRef.current;
    if (mounted === undefined) return;
    const nextTitle = "Renamed by the Next.js client";
    mounted.exports.rename(nextTitle);
    setTitle(mounted.model.get("title"));
  }

  async function disposeDashboard() {
    const mounted = mountRef.current;
    if (mounted === undefined) return;
    mountRef.current = undefined;
    try {
      await mounted.dispose();
      setTitle("Disposed");
      setStatus("disposed");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setStatus("error");
    }
  }

  return (
    <section
      className="panel"
      aria-labelledby="client-heading"
      data-widget-status={status}
      data-widget-title={title}
    >
      <p className="step">2 · Browser-mounted view</p>
      <h2 id="client-heading">Interactive dashboard</h2>
      <p className="status" role="status" data-widget-status-text>
        {statusText(status, title, error)}
      </p>
      <div ref={hostRef} className="widget-host" data-widget-host />
      <div className="actions">
        <button
          type="button"
          data-widget-action="rename"
          onClick={renameDashboard}
          disabled={status !== "ready"}
        >
          Rename through initialize export
        </button>
        <button
          type="button"
          data-widget-action="dispose"
          onClick={() => void disposeDashboard()}
          disabled={status !== "ready"}
        >
          Dispose widget
        </button>
      </div>
    </section>
  );
}

function statusText(status: Status, title: string, error: string | undefined): string {
  switch (status) {
    case "ready":
      return `Mounted: ${title}`;
    case "disposed":
      return "Disposed: views, listeners, styles, and module URLs released";
    case "error":
      return error ?? "Widget mount failed.";
    case "loading":
      return "Loading widget…";
  }
}
