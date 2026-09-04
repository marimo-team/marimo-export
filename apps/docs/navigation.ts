interface NavigationPage {
  readonly text: string;
  readonly link: string;
}

interface NavigationGroup {
  readonly text: string;
  readonly collapsed?: boolean;
  readonly items: readonly NavigationItem[];
}

type NavigationItem = NavigationPage | NavigationGroup;

interface DocumentationSection {
  readonly key: string;
  readonly text: string;
  readonly prefix: string;
  readonly items: readonly NavigationItem[];
}

export const documentationSections = [
  {
    key: "introduction",
    text: "Start",
    prefix: "/",
    items: [
      { text: "marimo-export", link: "/" },
      { text: "When to use marimo-export", link: "/why" },
      { text: "What is marimo-export?", link: "/overview" },
    ],
  },
  {
    key: "concepts",
    text: "Concepts",
    prefix: "/concepts/",
    items: [
      { text: "Understand notebook exports", link: "/concepts/" },
      { text: "Choose notebook states", link: "/concepts/states-and-inputs" },
      {
        text: "Store and load outputs",
        link: "/concepts/outputs-and-representations",
      },
      { text: "Reuse earlier results", link: "/concepts/preparation-and-reuse" },
      { text: "How notebook caching fits", link: "/concepts/caching" },
      {
        text: "Update an application with new exports",
        link: "/concepts/exports-and-publications",
      },
      { text: "Verify and trust an export", link: "/concepts/integrity-and-trust" },
    ],
  },
  {
    key: "guides",
    text: "Guides",
    prefix: "/guide/",
    items: [
      { text: "Choose a guide", link: "/guide/" },
      {
        text: "Start",
        items: [
          { text: "Build your first export", link: "/guide/getting-started" },
          { text: "Run the market dashboard", link: "/guide/market-dashboard" },
        ],
      },
      {
        text: "Author",
        items: [
          { text: "Choose states and outputs", link: "/guide/choose-states" },
          { text: "Build or capture", link: "/guide/build-and-capture" },
        ],
      },
      {
        text: "Consume",
        items: [
          { text: "Read an export", link: "/guide/consume-an-export" },
          { text: "Build a browser application", link: "/guide/browser-applications" },
          { text: "Use exports with agents", link: "/guide/agents-and-automation" },
        ],
      },
      {
        text: "Integrate",
        items: [
          { text: "Serve a prepared publication", link: "/guide/prepared-publications" },
          { text: "Create a representation", link: "/guide/custom-representations" },
        ],
      },
      {
        text: "Operate",
        items: [
          { text: "Manage repository storage", link: "/guide/manage-repository" },
          { text: "Deploy an export", link: "/guide/deploy" },
          { text: "Troubleshoot", link: "/guide/troubleshooting" },
        ],
      },
    ],
  },
  {
    key: "reference",
    text: "Reference",
    prefix: "/reference/",
    items: [
      { text: "Reference overview", link: "/reference/" },
      { text: "StateSpace and ExportSpec", link: "/reference/export-spec" },
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
      { text: "Compatibility", link: "/reference/compatibility" },
      { text: "Terminology", link: "/reference/terminology" },
    ],
  },
] satisfies readonly DocumentationSection[];

export const flattenNavigationPages = (items: readonly NavigationItem[]): NavigationPage[] =>
  items.flatMap((item) => ("link" in item ? [item] : flattenNavigationPages(item.items)));

export const documentationPages = documentationSections.flatMap((section) =>
  flattenNavigationPages(section.items),
);

const pageByLink = new Map(documentationPages.map((page) => [page.link, page]));

const page = (link: string): NavigationPage => {
  const item = pageByLink.get(link);
  if (!item) {
    throw new Error(`Navigation route is not declared: ${link}`);
  }
  return item;
};

export const topNavigation = [
  {
    text: "Start",
    items: [
      { text: "Build your first export", link: page("/guide/getting-started").link },
      { text: "When to use marimo-export", link: page("/why").link },
      { text: "What is marimo-export?", link: page("/overview").link },
      { text: "Choose a guide", link: page("/guide/").link },
    ],
  },
  {
    text: "Concepts",
    link: page("/concepts/").link,
    activeMatch: "^/(?:overview|concepts(?:/|$))",
  },
  { text: "Guides", link: page("/guide/").link, activeMatch: "^/guide/" },
  { text: "Reference", link: page("/reference/").link, activeMatch: "^/reference/" },
  {
    text: "Project",
    items: [
      {
        text: "Issues and support",
        link: "https://github.com/marimo-team/marimo-export/issues",
      },
      {
        text: "Security policy",
        link: "https://github.com/marimo-team/marimo-export/blob/main/SECURITY.md",
      },
      {
        text: "Contributing",
        link: "https://github.com/marimo-team/marimo-export/blob/main/CONTRIBUTING.md",
      },
    ],
  },
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
