import { defineConfig } from "vite-plus";

import { antiSlopIgnorePatterns, antiSlopRules } from "./tools/oxlint/anti-slop/preset.ts";

const generated = [
  "**/dist/**",
  "**/node_modules/**",
  "packages/python/.pytest_cache/**",
  "tests/fixtures/export/*.json",
];

const ignored = [...generated, ...antiSlopIgnorePatterns];

export default defineConfig({
  staged: {
    "*": "vp check --fix",
  },
  fmt: {
    ignorePatterns: ignored,
    printWidth: 100,
  },
  lint: {
    categories: {
      correctness: "error",
      perf: "error",
    },
    ignorePatterns: ignored,
    jsPlugins: [
      { name: "anti-slop", specifier: "./tools/oxlint/anti-slop/index.ts" },
      { name: "vite-plus", specifier: "vite-plus/oxlint-plugin" },
    ],
    options: {
      denyWarnings: true,
      reportUnusedDisableDirectives: "error",
      typeAware: true,
      typeCheck: true,
    },
    plugins: ["typescript", "unicorn", "import"],
    rules: {
      ...antiSlopRules,
      "typescript/consistent-type-imports": [
        "error",
        { fixStyle: "separate-type-imports", prefer: "type-imports" },
      ],
      "import/no-cycle": "error",
      "import/no-duplicates": "error",
      "import/no-self-import": "error",
      "vite-plus/prefer-vite-plus-imports": "error",
    },
    overrides: [
      {
        files: ["**/*.test.ts", "**/test/**"],
        rules: {
          "typescript/no-misused-spread": "off",
          "typescript/unbound-method": "off",
        },
      },
      {
        files: ["packages/browser/src/**/*.ts", "packages/loader-*/src/**/*.ts"],
        rules: {
          "no-restricted-imports": [
            "error",
            {
              patterns: ["node:*"],
            },
          ],
        },
      },
      {
        files: ["packages/browser/src/**/*.ts"],
        rules: {
          "no-restricted-imports": [
            "error",
            {
              patterns: [
                "node:*",
                "#loaders/*",
                "@marimo-export/internal-loader-*",
                "@marimo-team/marimo-export/loader/*",
                "@anywidget/types",
                "@uwdata/flechette",
                "hyparquet",
                "lz4js",
                "vega-embed",
              ],
            },
          ],
        },
      },
      {
        files: ["packages/browser/src/loader/**/*.ts"],
        rules: {
          "no-restricted-imports": [
            "error",
            {
              patterns: [
                "node:*",
                "@marimo-export/internal-loader-*",
                "@marimo-team/marimo-export/loader/*",
                "@anywidget/types",
                "@uwdata/flechette",
                "hyparquet",
                "lz4js",
                "vega-embed",
              ],
            },
          ],
        },
      },
      {
        files: ["packages/browser/src/loader/anywidget.ts"],
        rules: {
          "no-restricted-imports": [
            "error",
            {
              paths: [
                "@marimo-export/internal-loader-arrow",
                "@marimo-export/internal-loader-numpy",
                "@marimo-export/internal-loader-parquet",
                "@marimo-export/internal-loader-vegalite",
              ],
              patterns: [
                "node:*",
                "@marimo-team/marimo-export/loader/*",
                "@anywidget/types",
                "@uwdata/flechette",
                "hyparquet",
                "lz4js",
                "vega-embed",
              ],
            },
          ],
        },
      },
      {
        files: ["packages/loader-*/src/**/*.ts"],
        rules: {
          "no-restricted-imports": [
            "error",
            {
              patterns: ["node:*", "#loaders/*", "@marimo-export/internal-loader-*"],
            },
          ],
        },
      },
    ],
  },
  run: {
    cache: true,
  },
});
