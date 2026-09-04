/// <reference types="vite/client" />

declare module "vitepress-mermaid-renderer/css" {
  const styles: string;
  export default styles;
}

declare module "*.vue" {
  import type { DefineComponent } from "vue";

  const component: DefineComponent;
  export default component;
}
