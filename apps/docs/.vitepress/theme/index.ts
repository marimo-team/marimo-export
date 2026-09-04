/// <reference types="vite/client" />

import { h, nextTick, watch } from "vue";
import DefaultTheme from "vitepress/theme";
import { inBrowser, type Theme, useData } from "vitepress";
import { createMermaidRenderer } from "vitepress-mermaid-renderer";

import { installAccessibilityEnhancements } from "./accessibility";
import StaticApp from "./components/StaticApp.vue";
import "vitepress-mermaid-renderer/css";
import "./custom.css";

export default {
  extends: DefaultTheme,
  Layout: () => {
    const { isDark } = useData();
    const configureMermaid = (): void => {
      const renderer = createMermaidRenderer({
        flowchart: { htmlLabels: false, useMaxWidth: true },
        securityLevel: "strict",
        startOnLoad: false,
        theme: isDark.value ? "dark" : "neutral",
      });
      renderer.setToolbar({
        downloadFormat: "svg",
        fullscreenMode: "dialog",
        showLanguageLabel: false,
        desktop: {
          copyCode: "enabled",
          resetView: "enabled",
          toggleFullscreen: "enabled",
          zoomIn: "enabled",
          zoomLevel: "enabled",
          zoomOut: "enabled",
        },
        mobile: {
          copyCode: "disabled",
          resetView: "disabled",
          toggleFullscreen: "disabled",
          zoomIn: "disabled",
          zoomLevel: "disabled",
          zoomOut: "disabled",
        },
      });
    };

    if (inBrowser) {
      void nextTick(configureMermaid);
      watch(isDark, configureMermaid);
    }

    return h(DefaultTheme.Layout);
  },
  enhanceApp({ app }) {
    app.component("StaticApp", StaticApp);
    if (inBrowser) installAccessibilityEnhancements();
  },
} satisfies Theme;
