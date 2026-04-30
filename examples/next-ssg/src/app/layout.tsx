import type { Metadata } from "next";
import type { ReactNode } from "react";

import { ThemeToggle } from "@/components/theme-toggle";
import { themeStorageKey } from "@/lib/theme";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: "marimo export SSG finance",
  description: "Static finance pages generated from a live marimo notebook export bundle.",
};

interface RootLayoutProps {
  children: ReactNode;
}

const themeScript = `
(() => {
  try {
    const theme = window.localStorage.getItem(${JSON.stringify(themeStorageKey)});
    if (theme === "light" || theme === "dark") {
      document.documentElement.dataset.theme = theme;
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
  } catch {
    document.documentElement.removeAttribute("data-theme");
  }
})();
`;

const RootLayout = ({ children }: RootLayoutProps) => (
  <html lang="en" suppressHydrationWarning>
    <head>
      <script id="theme-bootstrap" dangerouslySetInnerHTML={{ __html: themeScript }} />
    </head>
    <body>
      <ThemeToggle />
      {children}
    </body>
  </html>
);

export default RootLayout;
