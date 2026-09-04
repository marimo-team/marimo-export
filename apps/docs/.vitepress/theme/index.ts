/// <reference types="vite/client" />

import DefaultTheme from "vitepress/theme";
import { inBrowser, type Theme } from "vitepress";

import { installAccessibilityEnhancements } from "./accessibility";
import StaticApp from "./components/StaticApp.vue";
import "./custom.css";

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component("StaticApp", StaticApp);
    if (inBrowser) installAccessibilityEnhancements();
  },
} satisfies Theme;
