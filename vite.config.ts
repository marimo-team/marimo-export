import { defineConfig } from "vite-plus";

const generated = [
  "**/dist/**",
  "**/node_modules/**",
  "packages/python/.pytest_cache/**",
  "tests/fixtures/export/*.json",
];

export default defineConfig({
  staged: {
    "*": "vp check --fix",
  },
  fmt: {
    ignorePatterns: generated,
    printWidth: 100,
  },
  lint: {
    categories: {
      correctness: "error",
      perf: "error",
    },
    ignorePatterns: generated,
    jsPlugins: [{ name: "vite-plus", specifier: "vite-plus/oxlint-plugin" }],
    options: {
      typeAware: true,
      typeCheck: true,
    },
    plugins: ["typescript", "unicorn", "import"],
    rules: {
      "typescript/consistent-type-imports": [
        "error",
        { fixStyle: "separate-type-imports", prefer: "type-imports" },
      ],
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
    ],
  },
  run: {
    cache: true,
  },
});
