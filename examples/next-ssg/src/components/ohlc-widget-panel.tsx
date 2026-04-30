"use client";

import { useEffect, useRef, useState } from "react";

import {
  anywidgetLoader,
  createWidgetStore,
  type LoadedAnyWidget,
  type WidgetStore,
} from "@marimo-team/export-loader-anywidget";
import { readLatestExport } from "@marimo-team/export-reader";

import { exportPublicRoot } from "@/lib/export-paths";

interface PriceRow {
  Date: string;
  Symbol: string;
  Open: number;
  "Open Change": number | null;
  High: number;
  "High Change": number | null;
  Low: number;
  "Low Change": number | null;
  Close: number;
  "Close Change": number | null;
}

interface OhlcWidgetState {
  title: string;
  rows: PriceRow[];
  metric: "Open" | "High" | "Low" | "Close";
  mode: "absolute" | "change";
  selected_symbols: string[];
}

interface OhlcWidgetPanelProps {
  scenario: string;
}

export const OhlcWidgetPanel = ({ scenario }: OhlcWidgetPanelProps) => {
  const hostRef = useRef<HTMLDivElement>(null);
  const storeRef = useRef<WidgetStore<OhlcWidgetState> | null>(null);
  const [status, setStatus] = useState("loading anywidget");
  const [snapshot, setSnapshot] = useState<Readonly<OhlcWidgetState> | null>(null);

  useEffect(() => {
    let cancelled = false;
    let loaded: LoadedAnyWidget<OhlcWidgetState> | undefined;
    let unmount: (() => Promise<void>) | undefined;
    let unsubscribe: (() => void) | undefined;

    const mount = async () => {
      const host = hostRef.current;
      if (!host) {
        return;
      }

      setStatus("loading anywidget");
      setSnapshot(null);
      storeRef.current = null;
      host.replaceChildren();

      const exp = await readLatestExport({
        root: exportPublicRoot,
        loaders: [anywidgetLoader()],
      });
      loaded = await exp
        .get({ scenario, value: "ohlc_dashboard", format: "bundle" })
        .load<LoadedAnyWidget<OhlcWidgetState>>();

      if (cancelled) {
        return;
      }

      const mounted = await loaded.mount(host);
      unmount = mounted.unmount;

      const store = createWidgetStore<OhlcWidgetState>(mounted.widget);
      storeRef.current = store;
      setSnapshot(store.get());
      unsubscribe = store.subscribe(() => {
        setSnapshot(store.get());
      });
      setStatus("anywidget mounted");
    };

    mount().catch((error: unknown) => {
      setStatus(error instanceof Error ? error.message : "failed to mount anywidget");
    });

    return () => {
      cancelled = true;
      storeRef.current = null;
      unsubscribe?.();
      void unmount?.();
      loaded?.dispose();
    };
  }, [scenario]);

  const setMetric = (metric: OhlcWidgetState["metric"]) => {
    storeRef.current?.set("metric", metric);
  };

  const setMode = (mode: OhlcWidgetState["mode"]) => {
    storeRef.current?.set("mode", mode);
  };

  return (
    <div className="widget-panel">
      <div className="widget-bridge" aria-label="React widget state bridge">
        <div>
          <span>anywidget.bundle.v1</span>
          <strong>{status}</strong>
        </div>
        <div>
          <span>React snapshot</span>
          <strong data-widget-snapshot>
            {snapshot
              ? `${snapshot.metric} · ${snapshot.mode} · ${snapshot.selected_symbols.join(", ")}`
              : "waiting for model state"}
          </strong>
        </div>
      </div>

      <div className="react-controls" aria-label="React controls for hydrated anywidget">
        {(["Open", "High", "Low", "Close"] as const).map((metric) => (
          <button
            key={metric}
            type="button"
            aria-pressed={snapshot?.metric === metric}
            data-active={snapshot?.metric === metric}
            onClick={() => setMetric(metric)}
          >
            {metric}
          </button>
        ))}
        <button
          type="button"
          aria-pressed={snapshot?.mode === "absolute"}
          data-active={snapshot?.mode === "absolute"}
          onClick={() => setMode("absolute")}
        >
          Absolute
        </button>
        <button
          type="button"
          aria-pressed={snapshot?.mode === "change"}
          data-active={snapshot?.mode === "change"}
          onClick={() => setMode("change")}
        >
          Change
        </button>
      </div>

      <div ref={hostRef} className="widget-host" />
    </div>
  );
};
