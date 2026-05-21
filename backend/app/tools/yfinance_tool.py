"""Yahoo Finance wrapper.

`yfinance` is a sync, scrapy-style client (no API key, no rate limit
contract). We:
- run its calls on a worker thread via asyncio.to_thread (it's sync internally)
- normalise NaN -> None at the boundary so downstream code never sees NaN
- cache results in-process for 1 hour to avoid hammering Yahoo

Two entry points:
- get_price_history(ticker, period="1y") -> PriceHistory
- get_fundamentals(ticker)                 -> Fundamentals
"""

import asyncio
import math
import time
from datetime import date
from typing import Any, Final

import yfinance as yf
from pydantic import BaseModel, Field

_CACHE_TTL_SECONDS: Final[float] = 3600.0  # 1 hour

_price_cache: dict[tuple[str, str], tuple[float, "PriceHistory"]] = {}
_fundamentals_cache: dict[str, tuple[float, "Fundamentals"]] = {}


def _nan_to_none(value: Any) -> float | None:
    """Yahoo returns NaN for missing values; normalise to None."""
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(as_float):
        return None
    return as_float


class PricePoint(BaseModel):
    date: date
    close: float = Field(..., description="Adjusted closing price.")
    volume: int = Field(..., description="Trading volume for the day.")


class PriceHistory(BaseModel):
    ticker: str
    period: str = Field(..., description='Range string, e.g. "1mo", "1y", "5y".')
    points: list[PricePoint] = Field(default_factory=list)


class Fundamentals(BaseModel):
    ticker: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None
    forward_pe: float | None = None
    price_to_book: float | None = None
    profit_margin: float | None = None
    operating_margin: float | None = None
    revenue_growth: float | None = None
    debt_to_equity: float | None = None
    description: str | None = None


def _fetch_price_history_sync(ticker: str, period: str) -> PriceHistory:
    """Sync worker — call via asyncio.to_thread."""
    handle = yf.Ticker(ticker)
    df = handle.history(period=period, auto_adjust=True)

    points: list[PricePoint] = []
    for ts, row in df.iterrows():
        close = _nan_to_none(row.get("Close"))
        if close is None:
            continue
        volume_raw = _nan_to_none(row.get("Volume"))
        volume = int(volume_raw) if volume_raw is not None else 0
        points.append(PricePoint(date=ts.date(), close=close, volume=volume))

    return PriceHistory(ticker=ticker.upper(), period=period, points=points)


def _fetch_fundamentals_sync(ticker: str) -> Fundamentals:
    """Sync worker — call via asyncio.to_thread."""
    handle = yf.Ticker(ticker)
    info = handle.info or {}

    return Fundamentals(
        ticker=ticker.upper(),
        name=info.get("longName") or info.get("shortName"),
        sector=info.get("sector"),
        industry=info.get("industry"),
        market_cap=_nan_to_none(info.get("marketCap")),
        pe_ratio=_nan_to_none(info.get("trailingPE")),
        forward_pe=_nan_to_none(info.get("forwardPE")),
        price_to_book=_nan_to_none(info.get("priceToBook")),
        profit_margin=_nan_to_none(info.get("profitMargins")),
        operating_margin=_nan_to_none(info.get("operatingMargins")),
        revenue_growth=_nan_to_none(info.get("revenueGrowth")),
        debt_to_equity=_nan_to_none(info.get("debtToEquity")),
        description=info.get("longBusinessSummary"),
    )


async def get_price_history(ticker: str, *, period: str = "1y") -> PriceHistory:
    """Fetch daily price history for a ticker. Cached in-process for 1 hour."""
    key = (ticker.upper(), period)
    now = time.time()
    cached = _price_cache.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    result = await asyncio.to_thread(_fetch_price_history_sync, ticker, period)
    _price_cache[key] = (now, result)
    return result


async def get_fundamentals(ticker: str) -> Fundamentals:
    """Fetch fundamental metrics for a ticker. Cached in-process for 1 hour."""
    key = ticker.upper()
    now = time.time()
    cached = _fundamentals_cache.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    result = await asyncio.to_thread(_fetch_fundamentals_sync, ticker)
    _fundamentals_cache[key] = (now, result)
    return result
