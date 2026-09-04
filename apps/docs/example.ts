const repository = "https://github.com/marimo-team/marimo-export";

export const documentationExamples = {
  market: {
    defaultTab: "application",
    source: {
      application: `${repository}/tree/main/examples/vite-vanilla/src`,
      notebook: `${repository}/blob/main/examples/vite-vanilla/finance.py`,
      spec: `${repository}/blob/main/examples/vite-vanilla/finance.export.yaml`,
    },
    tabs: [
      {
        boundary: "Original notebook and captured outputs",
        href: "/examples/market-dashboard/notebook/index.html",
        key: "notebook",
        label: "Notebook",
        status: "Static notebook",
        title: "Original finance notebook",
      },
      {
        boundary: "No Python runtime or notebook source",
        href: "/examples/market-dashboard/application/index.html",
        key: "application",
        label: "Exported app",
        status: "Static application",
        title: "Technology watchlist static application",
      },
    ],
  },
  quickstart: {
    defaultTab: "application",
    source: {
      application: `${repository}/tree/main/examples/quickstart/src`,
      notebook: `${repository}/blob/main/examples/quickstart/report.py`,
      spec: `${repository}/blob/main/examples/quickstart/report.export.yaml`,
    },
    tabs: [
      {
        boundary: "Original notebook and captured outputs",
        href: "/examples/quickstart/notebook/index.html",
        key: "notebook",
        label: "Notebook",
        status: "Static notebook",
        title: "Original quickstart notebook",
      },
      {
        boundary: "No Python runtime or notebook source",
        href: "/examples/quickstart/application/index.html",
        key: "application",
        label: "Exported app",
        status: "Static application",
        title: "Prepared report static application",
      },
    ],
  },
} as const;

export type DocumentationExampleName = keyof typeof documentationExamples;
