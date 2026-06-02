"use client";

import { useEffect, useRef, useState } from "react";

import { vegaliteLoader, type VegaLiteRenderResult } from "@marimo-team/export-loader-vegalite";
import { readExport } from "@marimo-team/export-reader";

import { exportPublicRoot } from "@/lib/export-paths";

interface VegaLiteChartProps {
  scenario: string;
}

export const VegaLiteChart = ({ scenario }: VegaLiteChartProps) => {
  const hostRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState("loading interactive Vega-Lite");

  useEffect(() => {
    let cancelled = false;
    let result: VegaLiteRenderResult | undefined;

    const render = async () => {
      const host = hostRef.current;
      if (!host) {
        return;
      }

      setStatus("loading interactive Vega-Lite");
      host.replaceChildren();

      const exp = await readExport({
        root: exportPublicRoot,
        loaders: [vegaliteLoader({ actions: true })],
      });
      const chart = await exp
        .get({ scenario, value: "chart", format: "vegalite" })
        .load(vegaliteLoader({ actions: true }));

      if (cancelled) {
        return;
      }

      result = await chart.render(host, {
        actions: true,
        renderer: "canvas",
      });
      setStatus("interactive chart ready");
    };

    render().catch((error: unknown) => {
      setStatus(error instanceof Error ? error.message : "failed to render chart");
    });

    return () => {
      cancelled = true;
      result?.finalize();
    };
  }, [scenario]);

  return (
    <div className="chart-panel">
      <div className="chart-toolbar">
        <span>vegalite.v1</span>
        <span>{status}</span>
      </div>
      <div ref={hostRef} className="vega-host" />
    </div>
  );
};
