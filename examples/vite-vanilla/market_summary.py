from __future__ import annotations

import json
from typing import Any

import polars as pl
from marimo_export import BlobAsset

_MEDIA_TYPE = "application/vnd.marimo-export.market-summary.v1+json"
_SCHEMA = "marimo-export.market-summary.v1"


def encode(prices: pl.DataFrame, *, currency: str) -> BlobAsset:
    """Return the period summary consumed by the market dashboard."""

    if prices.is_empty():
        raise ValueError("prices must contain at least one row")
    if len(currency) != 3 or not currency.isascii() or not currency.isupper():
        raise ValueError("currency must be a three-letter uppercase code")
    dates = prices.get_column("Date")
    first_session = dates.min()
    last_session = dates.max()
    if first_session is None or last_session is None:
        raise ValueError("prices must contain dated rows")

    returns = (
        prices.sort(["Symbol", "Date"])
        .group_by("Symbol", maintain_order=True)
        .agg(
            pl.col("Close").first().alias("first_close"),
            pl.col("Close").last().alias("latest_close"),
        )
        .with_columns((pl.col("latest_close") / pl.col("first_close") - 1).alias("period_return"))
        .select("Symbol", "period_return")
        .sort("period_return", descending=True)
    )
    period_returns = [
        {"symbol": str(row["Symbol"]), "return": float(row["period_return"])}
        for row in returns.iter_rows(named=True)
    ]
    average_return = returns.get_column("period_return").mean()
    if average_return is None:
        raise ValueError("prices must contain closing prices")

    payload: dict[str, Any] = {
        "schema": _SCHEMA,
        "currency": currency,
        "first_session": first_session.isoformat(),
        "last_session": last_session.isoformat(),
        "session_count": dates.n_unique(),
        "company_count": returns.height,
        "observation_count": prices.height,
        "average_return": float(average_return),
        "leader": period_returns[0],
        "period_returns": period_returns,
    }
    return BlobAsset(
        data=json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
        media_type=_MEDIA_TYPE,
        filename="market-summary.json",
        metadata={"schema": _SCHEMA},
    )
