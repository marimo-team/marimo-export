export type CounterState = Record<string, unknown> & {
  readonly accent: string;
  readonly count: number;
  readonly label: string;
  readonly payload: DataView;
};
