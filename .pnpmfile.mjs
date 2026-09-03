const PUBLIC_BROWSER_PACKAGE = "@marimo-team/marimo-export";
const INTERNAL_ANYWIDGET_PACKAGE = "@marimo-export/internal-loader-anywidget";

const beforePacking = (manifest) => {
  if (manifest.name !== PUBLIC_BROWSER_PACKAGE || manifest.dependencies === undefined) {
    return manifest;
  }
  const dependencies = { ...manifest.dependencies };
  delete dependencies[INTERNAL_ANYWIDGET_PACKAGE];
  return { ...manifest, dependencies };
};

export const hooks = { beforePacking };
