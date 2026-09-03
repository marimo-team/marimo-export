import { defineBlobAssetLoader } from "@marimo-team/marimo-export";
import { portableJsonObject } from "@marimo-team/portable-json";
import type { JsonObject, JsonValue } from "@marimo-team/portable-json";

export interface PeriodReturn {
  readonly return: number;
  readonly symbol: string;
}

export interface MarketSummary {
  readonly averageReturn: number;
  readonly companyCount: number;
  readonly currency: string;
  readonly firstSession: number;
  readonly lastSession: number;
  readonly leader: PeriodReturn;
  readonly observationCount: number;
  readonly periodReturns: readonly PeriodReturn[];
  readonly sessionCount: number;
}

const MEDIA_TYPE = "application/vnd.marimo-export.market-summary.v1+json";
const SCHEMA = "marimo-export.market-summary.v1";

export function marketSummaryLoader() {
  return defineBlobAssetLoader<MarketSummary>({
    mediaTypes: MEDIA_TYPE,
    load({ payload, signal }) {
      signal?.throwIfAborted();
      const value = portableJsonObject(
        JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(payload.data)),
        "market summary",
      );
      if (value.schema !== SCHEMA) throw new Error("The market summary schema is unsupported.");
      const periodReturns = array(value.period_returns, "period_returns").map((item, index) => {
        const entry = record(item, `period_returns[${index}]`);
        return Object.freeze({
          return: finiteNumber(entry.return, `period_returns[${index}].return`),
          symbol: string(entry.symbol, `period_returns[${index}].symbol`),
        });
      });
      if (periodReturns.length === 0) throw new Error("The market summary has no companies.");
      const leader = record(value.leader, "leader");
      const result = Object.freeze({
        averageReturn: finiteNumber(value.average_return, "average_return"),
        companyCount: positiveInteger(value.company_count, "company_count"),
        currency: string(value.currency, "currency"),
        firstSession: date(value.first_session, "first_session"),
        lastSession: date(value.last_session, "last_session"),
        leader: Object.freeze({
          return: finiteNumber(leader.return, "leader.return"),
          symbol: string(leader.symbol, "leader.symbol"),
        }),
        observationCount: positiveInteger(value.observation_count, "observation_count"),
        periodReturns: Object.freeze(periodReturns),
        sessionCount: positiveInteger(value.session_count, "session_count"),
      });
      signal?.throwIfAborted();
      return result;
    },
  });
}

function record(value: JsonValue | undefined, name: string): JsonObject {
  if (!isJsonObject(value)) {
    throw new Error(`${name} must be an object.`);
  }
  return value;
}

function array(value: JsonValue | undefined, name: string): readonly JsonValue[] {
  if (!Array.isArray(value)) throw new Error(`${name} must be an array.`);
  return value;
}

function string(value: JsonValue | undefined, name: string): string {
  if (!isJsonString(value) || value.length === 0) {
    throw new Error(`${name} must be a non-empty string.`);
  }
  return value;
}

function finiteNumber(value: JsonValue | undefined, name: string): number {
  if (!isJsonNumber(value) || !Number.isFinite(value)) {
    throw new Error(`${name} must be a finite number.`);
  }
  return value;
}

function positiveInteger(value: JsonValue | undefined, name: string): number {
  const result = finiteNumber(value, name);
  if (!Number.isInteger(result) || result < 1) throw new Error(`${name} must be positive.`);
  return result;
}

function date(value: JsonValue | undefined, name: string): number {
  const result = Date.parse(string(value, name));
  if (!Number.isFinite(result)) throw new Error(`${name} must be an ISO date.`);
  return result;
}

function isJsonObject(value: JsonValue | undefined): value is JsonObject {
  return (
    value !== null &&
    !Array.isArray(value) &&
    Object.prototype.toString.call(value) === "[object Object]"
  );
}

function isJsonString(value: JsonValue | undefined): value is string {
  return Object.prototype.toString.call(value) === "[object String]";
}

function isJsonNumber(value: JsonValue | undefined): value is number {
  return Object.prototype.toString.call(value) === "[object Number]";
}
