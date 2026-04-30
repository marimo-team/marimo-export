export const themeStorageKey = "marimo-export-example-theme";

export type ThemeChoice = "system" | "light" | "dark";

export const themeChoices = ["system", "light", "dark"] as const satisfies readonly ThemeChoice[];

export const isThemeChoice = (value: unknown): value is ThemeChoice =>
  value === "system" || value === "light" || value === "dark";
