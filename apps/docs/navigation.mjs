/**
 * @typedef {{ text: string, link: string }} NavigationPage
 * @typedef {{ text: string, collapsed?: boolean, items: NavigationItem[] }} NavigationGroup
 * @typedef {NavigationPage | NavigationGroup} NavigationItem
 * @typedef {{ key: string, text: string, prefix: string, items: NavigationItem[] }} DocumentationSection
 */

/** @type {DocumentationSection[]} */
export const documentationSections = [
  {
    key: "introduction",
    text: "Introduction",
    prefix: "/",
    items: [
      { text: "marimo-export", link: "/" },
      { text: "How notebook exports work", link: "/overview" },
      { text: "Why marimo-export", link: "/why" },
    ],
  },
  {
    key: "concepts",
    text: "Concepts",
    prefix: "/concepts/",
    items: [
      { text: "Concepts overview", link: "/concepts/" },
      { text: "States and inputs", link: "/concepts/states-and-inputs" },
      {
        text: "Outputs and representations",
        link: "/concepts/outputs-and-representations",
      },
      { text: "Preparation and reuse", link: "/concepts/preparation-and-reuse" },
      { text: "Integrity and trust", link: "/concepts/integrity-and-trust" },
    ],
  },
  {
    key: "guides",
    text: "Guides",
    prefix: "/guide/",
    items: [
      { text: "Guides overview", link: "/guide/" },
      { text: "Build your first notebook export", link: "/guide/getting-started" },
      { text: "Run the market dashboard", link: "/guide/market-dashboard" },
      { text: "Choose states and outputs", link: "/guide/choose-states" },
      { text: "Build or capture", link: "/guide/build-and-capture" },
      { text: "Manage the export repository", link: "/guide/manage-repository" },
      { text: "Consume a notebook export", link: "/guide/consume-an-export" },
      { text: "Build a browser application", link: "/guide/browser-applications" },
      { text: "Serve a prepared publication", link: "/guide/prepared-publications" },
      { text: "Create a custom representation", link: "/guide/custom-representations" },
      { text: "Use notebook exports with agents", link: "/guide/agents-and-automation" },
      { text: "Deploy a notebook export", link: "/guide/deploy" },
      { text: "Troubleshoot notebook exports", link: "/guide/troubleshooting" },
    ],
  },
  {
    key: "reference",
    text: "Reference",
    prefix: "/reference/",
    items: [
      { text: "Reference overview", link: "/reference/" },
      { text: "ExportSpec reference", link: "/reference/export-spec" },
      { text: "Output representations", link: "/reference/representations" },
      { text: "Export format reference", link: "/reference/export-format" },
      { text: "CLI reference", link: "/reference/cli" },
      {
        text: "Python",
        collapsed: true,
        items: [
          { text: "Python API", link: "/reference/python-api" },
          { text: "Produce an export from Python", link: "/reference/python/produce" },
          { text: "Read and verify exports from Python", link: "/reference/python/reader" },
          {
            text: "Sessions and inspection",
            link: "/reference/python/sessions-and-inspection",
          },
          {
            text: "Repository and observations",
            link: "/reference/python/repository-and-observations",
          },
          {
            text: "Delivery and publications",
            link: "/reference/python/delivery-and-publications",
          },
          { text: "Advanced host integration", link: "/reference/python/host-integration" },
          {
            text: "Format records and errors",
            link: "/reference/python/format-records-and-errors",
          },
        ],
      },
      {
        text: "Browser",
        collapsed: true,
        items: [
          { text: "Browser API reference", link: "/reference/browser-api" },
          { text: "Browser reader", link: "/reference/browser/reader" },
          {
            text: "Prepared publications",
            link: "/reference/browser/prepared-publications",
          },
          { text: "Output loaders", link: "/reference/browser/loaders" },
          { text: "marimo snapshots", link: "/reference/browser/snapshots" },
          { text: "Browser errors and limits", link: "/reference/browser/errors-and-limits" },
        ],
      },
      { text: "Portable JSON", link: "/reference/portable-json" },
      { text: "Terminology", link: "/reference/terminology" },
    ],
  },
];

/**
 * @param {NavigationItem[]} items
 * @returns {NavigationPage[]}
 */
export const flattenNavigationPages = (items) =>
  items.flatMap((item) => ("link" in item ? [item] : flattenNavigationPages(item.items)));

export const documentationPages = documentationSections.flatMap((section) =>
  flattenNavigationPages(section.items),
);

const pageByLink = new Map(documentationPages.map((page) => [page.link, page]));

/** @param {string} link */
const page = (link) => {
  const item = pageByLink.get(link);
  if (!item) {
    throw new Error(`Navigation route is not declared: ${link}`);
  }
  return item;
};

export const topNavigation = [
  { text: "Get started", link: page("/guide/getting-started").link },
  { text: "Concepts", link: page("/concepts/").link },
  { text: "Guides", link: page("/guide/").link },
  { text: "Reference", link: page("/reference/").link },
];

export const documentationSidebar = Object.fromEntries(
  documentationSections.map((activeSection) => [
    activeSection.prefix,
    documentationSections.map((section) => ({
      text: section.text,
      collapsed: section.key !== activeSection.key,
      items: section.items,
    })),
  ]),
);

// The pinned LLM bundle plugin drops VitePress's deployment base when it
// descends into nested sidebar groups. A flat copy preserves page order while
// keeping every generated URL under the configured base.
export const llmsSidebar = documentationSections.map(({ text, items }) => ({
  text,
  items: flattenNavigationPages(items),
}));
