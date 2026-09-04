<script setup lang="ts">
import { withBase } from "vitepress";
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { documentationExamples, type DocumentationExampleName } from "../../../example.ts";

type TabKey = "application" | "notebook";

const props = withDefaults(
  defineProps<{ compact?: boolean; example?: DocumentationExampleName }>(),
  {
    compact: false,
    example: "market",
  },
);

const documentationExample = computed(() => documentationExamples[props.example]);
const selectedKey = ref<TabKey>(documentationExample.value.defaultTab);
const loaded = ref(false);
const frame = ref<HTMLIFrameElement>();
const frameHeight = ref(720);
const frameKey = ref(0);
let resizeObserver: ResizeObserver | undefined;
let mutationObserver: MutationObserver | undefined;

const selected = computed(
  () =>
    documentationExample.value.tabs.find((candidate) => candidate.key === selectedKey.value) ??
    documentationExample.value.tabs[0]!,
);
const href = computed(() => withBase(selected.value.href));

const expandNotebookDocument = (document: Document): HTMLElement | undefined => {
  if (selected.value.key !== "notebook") return undefined;
  const app = document.querySelector<HTMLElement>("#App");
  if (app === null) return undefined;
  // Marimo's static HTML fixes its root to the iframe viewport. Expand its
  // same-origin wrappers so the documentation page owns vertical scrolling.
  let element: HTMLElement | null = app;
  while (element !== null) {
    element.style.setProperty("height", "auto", "important");
    element.style.setProperty("min-height", "0", "important");
    element.style.setProperty("width", "100%", "important");
    element.style.setProperty("max-width", "100%", "important");
    element.style.setProperty("overflow-x", "hidden", "important");
    element.style.setProperty("overflow-y", "visible", "important");
    if (element === document.body) break;
    element = element.parentElement;
  }
  document.documentElement.style.setProperty("height", "auto", "important");
  document.documentElement.style.setProperty("width", "100%", "important");
  document.documentElement.style.setProperty("max-width", "100%", "important");
  document.documentElement.style.setProperty("overflow-x", "hidden", "important");
  document.documentElement.style.setProperty("overflow-y", "visible", "important");
  return app;
};

const measure = (): void => {
  const document = frame.value?.contentDocument;
  if (!document?.body) return;
  const notebook = expandNotebookDocument(document);
  if (notebook !== undefined) {
    frameHeight.value = Math.ceil(notebook.getBoundingClientRect().top + notebook.scrollHeight);
    return;
  }
  frameHeight.value = Math.max(document.body.scrollHeight, document.body.offsetHeight);
};

const resetFrame = (key: TabKey): void => {
  resizeObserver?.disconnect();
  mutationObserver?.disconnect();
  selectedKey.value = key;
  loaded.value = false;
  frameHeight.value = 720;
  frameKey.value += 1;
};

const select = (key: TabKey): void => {
  if (selectedKey.value === key) return;
  resetFrame(key);
};

watch(
  () => props.example,
  () => resetFrame(documentationExample.value.defaultTab),
);

const markLoaded = (): void => {
  resizeObserver?.disconnect();
  mutationObserver?.disconnect();
  const document = frame.value?.contentDocument;
  if (!document?.body) return;
  const observe = (): boolean => {
    const notebook = expandNotebookDocument(document);
    if (selected.value.key === "notebook" && notebook === undefined) return false;
    measure();
    resizeObserver = new ResizeObserver(measure);
    resizeObserver.observe(notebook ?? document.body);
    loaded.value = true;
    return true;
  };
  if (observe()) return;
  mutationObserver = new MutationObserver(() => {
    if (!observe()) return;
    mutationObserver?.disconnect();
  });
  mutationObserver.observe(document.body, { childList: true, subtree: true });
};

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  mutationObserver?.disconnect();
});
</script>

<template>
  <figure class="static-app">
    <header
      class="static-app__controls"
      :class="{ 'static-app__controls--compact': props.compact }"
    >
      <nav aria-label="Trace the notebook source to its exported application">
        <template v-for="(tab, index) in documentationExample.tabs" :key="tab.key">
          <span v-if="index > 0" class="static-app__flow" aria-hidden="true">→</span>
          <button type="button" :aria-pressed="selected.key === tab.key" @click="select(tab.key)">
            {{ tab.label }}
          </button>
        </template>
      </nav>

      <a
        class="static-app__open"
        :href="href"
        target="_blank"
        rel="noopener noreferrer"
        :aria-label="`Open ${selected.label.toLowerCase()} full page`"
      >
        Open <span aria-hidden="true">↗</span>
      </a>

      <span v-if="!props.compact" class="static-app__runtime" aria-live="polite">
        <span class="static-app__status"><span aria-hidden="true" /> {{ selected.status }}</span>
        <span class="static-app__runtime-boundary">{{ selected.boundary }}</span>
      </span>

      <span v-if="!props.compact" class="static-app__source-links">
        <strong>Source</strong>
        <a :href="documentationExample.source.notebook" target="_blank" rel="noopener noreferrer">
          Notebook
        </a>
        <a :href="documentationExample.source.spec" target="_blank" rel="noopener noreferrer">
          ExportSpec
        </a>
        <a
          :href="documentationExample.source.application"
          target="_blank"
          rel="noopener noreferrer"
        >
          Application
        </a>
      </span>
    </header>

    <div class="static-app__viewport" :data-kind="selected.key" :aria-busy="!loaded">
      <div v-if="!loaded" class="static-app__loading" role="status">
        Loading {{ selected.label.toLowerCase() }}…
      </div>
      <iframe
        :key="frameKey"
        ref="frame"
        :src="href"
        :style="{ height: `${frameHeight}px` }"
        :title="selected.title"
        allow="clipboard-write; fullscreen"
        allowfullscreen
        loading="eager"
        referrerpolicy="strict-origin-when-cross-origin"
        scrolling="no"
        @load="markLoaded"
      />
    </div>
  </figure>
</template>

<style scoped>
.static-app {
  container-type: inline-size;
  display: grid;
  grid-template-rows: auto auto;
  margin: 2rem 0 3rem;
  overflow: hidden;
  border: 1px solid var(--vp-c-divider);
  border-radius: 8px;
  background: var(--vp-c-bg-elv);
}

.static-app__controls {
  display: grid;
  grid-template-areas:
    "tabs open"
    "runtime source";
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.35rem 2rem;
  align-items: baseline;
  min-height: 4rem;
  padding: 0.55rem 1rem 0.65rem;
  color: var(--vp-c-text-3);
  font-family: var(--vp-font-family-mono);
  font-size: 0.64rem;
}

.static-app__controls--compact {
  grid-template-areas: "tabs open";
  min-height: 0;
  padding-block: 0.65rem;
}

.static-app__controls nav {
  grid-area: tabs;
  display: inline-flex;
  gap: 0.35rem;
  align-items: center;
  justify-self: start;
}

.static-app__controls button {
  border: 0;
  border-radius: 4px;
  padding: 0.28rem 0.42rem;
  background: transparent;
  color: var(--vp-c-text-2);
  cursor: pointer;
  font: inherit;
  font-weight: 650;
}

.static-app__controls button:hover {
  background: var(--vp-c-bg-soft);
  color: var(--vp-c-text-1);
}

.static-app__controls button[aria-pressed="true"] {
  background: var(--vp-c-brand-soft);
  color: var(--vp-c-brand-1);
}

.static-app__flow {
  color: var(--vp-c-text-3);
  font-size: 0.72rem;
}

.static-app__runtime {
  grid-area: runtime;
  display: inline-flex;
  gap: 0.65rem;
  align-items: baseline;
  min-width: 0;
}

.static-app__status {
  display: inline-flex;
  gap: 0.5rem;
  align-items: center;
  color: var(--vp-c-brand-1);
  font-weight: 650;
  white-space: nowrap;
}

.static-app__status > span {
  width: 0.42rem;
  height: 0.42rem;
  flex: 0 0 auto;
  border-radius: 50%;
  background: currentcolor;
}

.static-app__runtime-boundary {
  color: var(--vp-c-text-3);
  font-size: 0.6rem;
}

.static-app__source-links {
  grid-area: source;
  display: inline-flex;
  gap: 0.55rem;
  align-items: baseline;
  justify-self: end;
}

.static-app__source-links strong {
  color: var(--vp-c-text-2);
}

.static-app__controls a {
  color: var(--vp-c-text-2);
  text-decoration: none;
}

.static-app__open {
  grid-area: open;
  display: inline-flex;
  gap: 0.35rem;
  align-items: baseline;
  justify-self: end;
  color: var(--vp-c-text-1) !important;
  font-weight: 650;
}

.static-app__controls a:hover {
  color: var(--vp-c-brand-1) !important;
}

.static-app__controls a:focus-visible,
.static-app__controls button:focus-visible {
  outline: 3px solid color-mix(in srgb, var(--vp-c-brand-1) 45%, transparent);
  outline-offset: 3px;
}

.static-app__viewport {
  position: relative;
  border-top: 1px solid var(--vp-c-divider);
  background: #f8fafc;
  line-height: 0;
}

.static-app__viewport iframe {
  display: block;
  width: 100%;
  border: 0;
  background: #f8fafc;
}

.static-app__loading {
  position: absolute;
  inset: 0;
  z-index: 1;
  display: grid;
  place-content: center;
  background: var(--vp-c-bg-soft);
  color: var(--vp-c-brand-1);
  font-family: var(--vp-font-family-mono);
  font-size: 0.68rem;
  font-weight: 650;
  letter-spacing: 0.06em;
  line-height: 1.5;
  text-transform: uppercase;
}

@container (max-width: 48rem) {
  .static-app__controls:not(.static-app__controls--compact) {
    grid-template-areas:
      "tabs open"
      "runtime runtime"
      "source source";
    gap: 0.35rem 0.75rem;
  }

  .static-app__source-links {
    justify-self: start;
  }
}

@media (max-width: 639px) {
  .static-app {
    margin-inline: -1rem;
    border-right: 0;
    border-left: 0;
    border-radius: 0;
  }

  .static-app__controls:not(.static-app__controls--compact) {
    grid-template-areas:
      "tabs open"
      "runtime runtime";
    gap: 0.35rem 0.75rem;
    padding-inline: 1rem;
  }

  .static-app__runtime {
    flex-wrap: wrap;
    gap: 0.15rem 0.6rem;
  }

  .static-app__source-links {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .static-app *,
  .static-app *::before,
  .static-app *::after {
    scroll-behavior: auto !important;
  }
}
</style>
