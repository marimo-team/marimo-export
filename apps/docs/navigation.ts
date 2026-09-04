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
  readonly items: readonly NavigationItem[];
}

const introductionItems = [
  { text: "marimo-export", link: "/" },
  { text: "What is marimo-export?", link: "/overview" },
  { text: "When to use marimo-export", link: "/why" },
  { text: "Get started", link: "/guide/getting-started" },
] satisfies readonly NavigationItem[];

const guideItems = [
  {
    text: "Core ideas",
    collapsed: false,
    items: [
      { text: "Notebook states", link: "/concepts/states-and-inputs" },
      {
        text: "Outputs",
        link: "/concepts/outputs-and-representations",
      },
      { text: "Reuse", link: "/concepts/preparation-and-reuse" },
      { text: "Caching", link: "/concepts/caching" },
      {
        text: "Publishing updates",
        link: "/concepts/exports-and-publications",
      },
      { text: "Verification and trust", link: "/concepts/integrity-and-trust" },
    ],
  },
  {
    text: "Build and use",
    collapsed: true,
    items: [
      { text: "Choose states and outputs", link: "/guide/choose-states" },
      { text: "Build or capture", link: "/guide/build-and-capture" },
      { text: "Read an export", link: "/guide/consume-an-export" },
      { text: "Build a browser application", link: "/guide/browser-applications" },
      { text: "Use exports with agents", link: "/guide/agents-and-automation" },
    ],
  },
  {
    text: "Publish and operate",
    collapsed: true,
    items: [
      { text: "Serve a prepared publication", link: "/guide/prepared-publications" },
      { text: "Create a representation", link: "/guide/custom-representations" },
      { text: "Manage repository storage", link: "/guide/manage-repository" },
      { text: "Deploy an export", link: "/guide/deploy" },
      { text: "Troubleshoot", link: "/guide/troubleshooting" },
    ],
  },
] satisfies readonly NavigationItem[];

const exampleItems = [
  { text: "Market dashboard", link: "/guide/market-dashboard" },
] satisfies readonly NavigationItem[];

const referenceItems = [
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
] satisfies readonly NavigationItem[];

export const documentationSections = [
  { key: "introduction", text: "Introduction", items: introductionItems },
  { key: "guide", text: "Guide", items: guideItems },
  { key: "examples", text: "Examples", items: exampleItems },
  { key: "reference", text: "Reference", items: referenceItems },
] satisfies readonly DocumentationSection[];

export const flattenNavigationPages = (items: readonly NavigationItem[]): NavigationPage[] =>
  items.flatMap((item) => ("link" in item ? [item] : flattenNavigationPages(item.items)));

export const documentationPages = documentationSections.flatMap((section) =>
  flattenNavigationPages(section.items),
);

const pageByLink = new Map(documentationPages.map((page) => [page.link, page]));

const page = (link: string): NavigationPage => {
  const item = pageByLink.get(link);
  if (!item) throw new Error(`Navigation route is not declared: ${link}`);
  return item;
};

export const topNavigation = [
  {
    text: "Overview",
    link: page("/overview").link,
    activeMatch: "^/(?:overview|why)$",
  },
  {
    text: "Guide",
    items: [
      { text: "Get started", link: page("/guide/getting-started").link },
      { text: "Notebook states", link: page("/concepts/states-and-inputs").link },
      {
        text: "Outputs",
        link: page("/concepts/outputs-and-representations").link,
      },
      { text: "Build or capture", link: page("/guide/build-and-capture").link },
      { text: "Read an export", link: page("/guide/consume-an-export").link },
      { text: "Browser applications", link: page("/guide/browser-applications").link },
      { text: "Caching", link: page("/concepts/caching").link },
      {
        text: "Publishing updates",
        link: page("/concepts/exports-and-publications").link,
      },
    ],
  },
  {
    text: "Examples",
    link: page("/guide/market-dashboard").link,
    activeMatch: "^/guide/market-dashboard$",
  },
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

export const documentationSidebar = {
  "/reference/": [
    {
      text: "Reference",
      collapsed: false,
      items: referenceItems,
    },
  ],
  "/": [
    {
      text: "Introduction",
      collapsed: false,
      items: introductionItems.slice(1),
    },
    {
      text: "Guide",
      collapsed: false,
      items: guideItems,
    },
    {
      text: "Examples",
      collapsed: false,
      items: exampleItems,
    },
    {
      text: "Reference",
      items: [{ text: "API and file formats", link: page("/reference/").link }],
    },
  ],
};

// The pinned LLM bundle plugin drops VitePress's deployment base when it
// descends into nested sidebar groups. A flat copy preserves page order while
// keeping every generated URL under the configured base.
export const llmsSidebar = documentationSections.map(({ text, items }) => ({
  text,
  items: flattenNavigationPages(items),
}));
