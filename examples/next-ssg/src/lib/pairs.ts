export interface FinancePair {
  slug: string;
  symbols: readonly [string, string];
  title: string;
  note: string;
}

export const financePairs = [
  {
    slug: "crwv-msft",
    symbols: ["CRWV", "MSFT"],
    title: "CoreWeave vs Microsoft",
    note: "AI infrastructure exposure against a large-cap platform baseline.",
  },
  {
    slug: "aapl-msft",
    symbols: ["AAPL", "MSFT"],
    title: "Apple vs Microsoft",
    note: "Two mega-cap software and hardware franchises over the same window.",
  },
  {
    slug: "googl-amzn",
    symbols: ["GOOGL", "AMZN"],
    title: "Alphabet vs Amazon",
    note: "Cloud, ads, and commerce names rendered from the notebook source.",
  },
] as const satisfies readonly FinancePair[];

export const getFinancePair = (slug: string): FinancePair | undefined =>
  financePairs.find((pair) => pair.slug === slug);
