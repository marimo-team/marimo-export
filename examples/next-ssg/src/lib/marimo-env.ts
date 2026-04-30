export const marimoNotebook = (): string => process.env.MARIMO_NOTEBOOK ?? "notebooks/finance.py";

export const marimoServerUrl = (): string =>
  process.env.MARIMO_SERVER_URL ?? "http://localhost:8483";

export const marimoServerToken = (): string | undefined => {
  const token = process.env.MARIMO_SERVER_TOKEN?.trim();
  return token ? token : undefined;
};
