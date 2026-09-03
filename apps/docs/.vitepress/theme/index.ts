/// <reference types="vite/client" />

import DefaultTheme from "vitepress/theme";
import { inBrowser, type Theme } from "vitepress";

import { installAccessibilityEnhancements } from "./accessibility";
import "./custom.css";

export default {
  extends: DefaultTheme,
  enhanceApp() {
    if (inBrowser) installAccessibilityEnhancements();
  },
} satisfies Theme;
