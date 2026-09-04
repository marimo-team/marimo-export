export const documentationExample = {
  defaultTab: "application",
  source: {
    application: "https://github.com/marimo-team/marimo-export/tree/main/examples/vite-vanilla/src",
    notebook:
      "https://github.com/marimo-team/marimo-export/blob/main/examples/vite-vanilla/finance.py",
    spec: "https://github.com/marimo-team/marimo-export/blob/main/examples/vite-vanilla/finance.export.yaml",
  },
  tabs: [
    {
      boundary: "Original source · Captured outputs",
      href: "/examples/market-dashboard/notebook/index.html",
      key: "notebook",
      label: "Notebook",
      status: "Static HTML notebook",
      title: "Original finance notebook",
    },
    {
      boundary: "No WebAssembly · No server",
      href: "/examples/market-dashboard/application/index.html",
      key: "application",
      label: "Exported app",
      status: "Python-free static application",
      title: "Technology watchlist static application",
    },
  ],
} as const;
