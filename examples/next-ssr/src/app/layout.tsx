import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./style.css";

export const metadata: Metadata = {
  icons: { icon: "data:," },
  title: "Pythonless AnyWidget in Next.js",
  description: "A published marimo AnyWidget decoded on the server and mounted in the browser.",
};

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
