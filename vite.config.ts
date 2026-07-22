import { defineConfig } from "vite-plus";

const generated = [
  "**/.astro/**",
  "**/.next/**",
  "**/.output/**",
  "**/dist/**",
  "**/node_modules/**",
  "packages/producer/.pytest_cache/**",
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
        files: [
          "packages/client/src/index.ts",
          "packages/client/src/hash.ts",
          "packages/client/src/loader.ts",
          "packages/client/src/reader.ts",
          "packages/client/src/remote.ts",
          "packages/client/src/remote/**/*.ts",
          "packages/client/src/schema.ts",
          "packages/client/src/source.ts",
          "packages/client/src/types.ts",
        ],
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
