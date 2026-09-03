const MOBILE_SIDEBAR = "(max-width: 959px)";
const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

const setInert = (element: HTMLElement | null, active: boolean): void => {
  if (element === null) return;
  element.inert = active;
  if (active) {
    element.setAttribute("aria-hidden", "true");
  } else {
    element.removeAttribute("aria-hidden");
  }
};

const setAttribute = (element: HTMLElement, name: string, value: string): void => {
  if (element.getAttribute(name) !== value) element.setAttribute(name, value);
};

const focusableChildren = (element: HTMLElement): HTMLElement[] =>
  Array.from(element.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (candidate) => candidate.getClientRects().length > 0 && candidate.closest("[inert]") === null,
  );

const normalizeSidebarGroups = (): void => {
  for (const item of document.querySelectorAll<HTMLElement>(
    ".VPSidebarItem.collapsible > .item[role='button']",
  )) {
    setAttribute(
      item,
      "aria-expanded",
      String(!item.parentElement?.classList.contains("collapsed")),
    );

    const caret = item.querySelector<HTMLElement>(":scope > .caret");
    if (caret === null) continue;
    caret.removeAttribute("role");
    caret.removeAttribute("tabindex");
    caret.removeAttribute("aria-label");
    setAttribute(caret, "aria-hidden", "true");
  }
};

const normalizeSearch = (search: HTMLElement): void => {
  const input = search.querySelector<HTMLElement>("#localsearch-input");
  if (input !== null) {
    setAttribute(input, "role", "combobox");
    setAttribute(input, "aria-expanded", "true");
  }

  for (const item of search.querySelectorAll<HTMLElement>(".results > li")) {
    const result = item.querySelector<HTMLElement>(":scope > a.result");
    if (result === null) continue;

    const index = result.dataset.index;
    if (index === undefined) continue;
    setAttribute(item, "role", "presentation");
    item.removeAttribute("id");
    item.removeAttribute("aria-selected");
    setAttribute(result, "id", `localsearch-item-${index}`);
    setAttribute(result, "role", "option");
    setAttribute(result, "aria-selected", String(result.classList.contains("selected")));
    setAttribute(result, "tabindex", "-1");
  }

  for (const key of search.querySelectorAll<HTMLElement>("kbd[aria-label]")) {
    const label = key.getAttribute("aria-label");
    key.removeAttribute("aria-label");
    const icon = key.querySelector<HTMLElement>(":scope > .navigate-icon");
    if (label !== null && icon !== null) {
      setAttribute(icon, "role", "img");
      setAttribute(icon, "aria-label", label);
    }
  }
};

export const installAccessibilityEnhancements = (): void => {
  const mobileSidebar = window.matchMedia(MOBILE_SIDEBAR);
  let lastApplicationFocus: HTMLElement | null = null;
  let searchTrigger: HTMLElement | null = null;
  let searchWasOpen = false;

  document.addEventListener("focusin", (event) => {
    const application = document.querySelector<HTMLElement>("#app");
    if (event.target instanceof HTMLElement && application?.contains(event.target)) {
      lastApplicationFocus = event.target;
    }
  });

  const synchronize = (): void => {
    document.querySelector<HTMLElement>(".VPHome")?.setAttribute("role", "main");
    normalizeSidebarGroups();

    const search = document.querySelector<HTMLElement>("body > .VPLocalSearchBox");
    const searchIsOpen = search !== null;
    if (searchIsOpen && !searchWasOpen) searchTrigger = lastApplicationFocus;
    if (search !== null) {
      setAttribute(search, "role", "dialog");
      setAttribute(search, "aria-modal", "true");
      setAttribute(search, "aria-label", "Search documentation");
      normalizeSearch(search);
    }
    setInert(document.querySelector<HTMLElement>("#app"), searchIsOpen);

    if (!searchIsOpen && searchWasOpen) {
      const trigger = searchTrigger;
      searchTrigger = null;
      queueMicrotask(() => trigger?.focus());
    }
    searchWasOpen = searchIsOpen;

    const topNavigationOpen =
      document.querySelector<HTMLElement>('.VPNavBarHamburger[aria-expanded="true"]') !== null;
    const sidebarOpen =
      mobileSidebar.matches &&
      document.querySelector<HTMLElement>('.VPLocalNav .menu[aria-expanded="true"]') !== null;

    setInert(document.querySelector<HTMLElement>(".VPLocalNav"), topNavigationOpen);
    setInert(
      document.querySelector<HTMLElement>(".VPSidebar"),
      topNavigationOpen || (mobileSidebar.matches && !sidebarOpen),
    );
    setInert(document.querySelector<HTMLElement>("#VPContent"), topNavigationOpen || sidebarOpen);
    setInert(document.querySelector<HTMLElement>(".VPFooter"), topNavigationOpen || sidebarOpen);
  };

  document.addEventListener("keydown", (event) => {
    const navigationToggle = document.querySelector<HTMLElement>(
      '.VPNavBarHamburger[aria-expanded="true"]',
    );
    const navigationScreen = document.querySelector<HTMLElement>("#VPNavScreen");

    if (navigationToggle !== null && navigationScreen !== null) {
      if (event.key === "Escape") {
        event.preventDefault();
        navigationToggle.click();
        queueMicrotask(() => navigationToggle.focus());
        return;
      }

      if (event.key === "Tab") {
        const items = [navigationToggle, ...focusableChildren(navigationScreen)];
        const active = document.activeElement;
        const activeIndex = active instanceof HTMLElement ? items.indexOf(active) : -1;
        const movingBeforeFirst = event.shiftKey && activeIndex <= 0;
        const movingAfterLast = !event.shiftKey && activeIndex === items.length - 1;
        if (activeIndex === -1 || movingBeforeFirst || movingAfterLast) {
          event.preventDefault();
          const target = movingBeforeFirst ? items.at(-1) : items[0];
          target?.focus();
        }
        return;
      }
    }

    if (
      event.key === " " &&
      event.target instanceof HTMLElement &&
      event.target.matches(".VPSidebarItem.collapsible > .item[role='button']")
    ) {
      event.preventDefault();
      event.target.click();
    }
  });

  const observer = new MutationObserver(synchronize);
  observer.observe(document.body, {
    attributeFilter: ["aria-expanded", "class"],
    attributes: true,
    childList: true,
    subtree: true,
  });
  mobileSidebar.addEventListener("change", synchronize);
  synchronize();
};
