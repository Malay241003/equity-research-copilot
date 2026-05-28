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

_CACHE_TTL_SECONDS: Final[float] = 3600.0  # 1 hour — fundamentals, price, earnings
_VIX_CACHE_TTL_SECONDS: Final[float] = 900.0  # 15 min — VIX moves intraday, want fresher

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
    # high / low added Phase 3.5+ so technicals.py can compute ATR
    # (Average True Range). Optional because older cached data may not
    # have them populated.
    high: float | None = Field(default=None, description="Intraday high.")
    low: float | None = Field(default=None, description="Intraday low.")


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
    # Added Phase 3.5 — sentiment / momentum / macro analyzers consume these.
    # All optional because yfinance returns them inconsistently across tickers.
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    beta: float | None = None
    dividend_yield: float | None = None
    short_ratio: float | None = None
    held_by_institutions: float | None = Field(
        default=None,
        description="Fraction of float held by institutions (0.0–1.0).",
    )
    analyst_recommendation: str | None = Field(
        default=None,
        description='yfinance recommendationKey, e.g. "buy", "hold", "strong_buy".',
    )
    num_analyst_opinions: int | None = None


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
        points.append(
            PricePoint(
                date=ts.date(),
                close=close,
                volume=volume,
                high=_nan_to_none(row.get("High")),
                low=_nan_to_none(row.get("Low")),
            )
        )

    return PriceHistory(ticker=ticker.upper(), period=period, points=points)


def _fetch_fundamentals_sync(ticker: str) -> Fundamentals:
    """Sync worker — call via asyncio.to_thread."""
    handle = yf.Ticker(ticker)
    info = handle.info or {}

    num_analysts_raw = info.get("numberOfAnalystOpinions")
    num_analysts = int(num_analysts_raw) if num_analysts_raw is not None else None

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
        fifty_two_week_high=_nan_to_none(info.get("fiftyTwoWeekHigh")),
        fifty_two_week_low=_nan_to_none(info.get("fiftyTwoWeekLow")),
        beta=_nan_to_none(info.get("beta")),
        dividend_yield=_nan_to_none(info.get("dividendYield")),
        short_ratio=_nan_to_none(info.get("shortRatio")),
        held_by_institutions=_nan_to_none(info.get("heldPercentInstitutions")),
        analyst_recommendation=info.get("recommendationKey"),
        num_analyst_opinions=num_analysts,
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


# ─── VIX (market-wide volatility) ───────────────────────────────────


class VixLevel(BaseModel):
    """Latest CBOE VIX close. Market-wide, not company-specific."""

    level: float = Field(..., description="Latest closing VIX level.")
    as_of: date = Field(..., description="Trading date the level was sampled.")


_vix_cache: tuple[float, VixLevel] | None = None


def _fetch_vix_sync() -> VixLevel | None:
    """Sync worker — pull the most recent VIX close. None on any failure."""
    try:
        handle = yf.Ticker("^VIX")
        # 5d window gives us at least one close even if today is a weekend.
        df = handle.history(period="5d", auto_adjust=False)
        if df.empty:
            return None
        last_row = df.iloc[-1]
        last_close = _nan_to_none(last_row.get("Close"))
        if last_close is None:
            return None
        last_date = df.index[-1].date()
        return VixLevel(level=last_close, as_of=last_date)
    except Exception:  # noqa: BLE001 — yfinance can throw arbitrary errors
        return None


async def get_vix() -> VixLevel | None:
    """Latest VIX close, cached 1h in-process. None if Yahoo failed.

    Shared across all in-flight research runs in the same process — VIX
    is market-wide so re-fetching per ticker would waste bandwidth and
    risk a stale-vs-fresh discrepancy across sections of the same report.
    """
    global _vix_cache
    now = time.time()
    if _vix_cache and (now - _vix_cache[0]) < _VIX_CACHE_TTL_SECONDS:
        return _vix_cache[1]

    result = await asyncio.to_thread(_fetch_vix_sync)
    if result is not None:
        _vix_cache = (now, result)
    return result


# ─── Earnings history (last 4 quarters of EPS beats/misses) ─────────


class EarningsQuarter(BaseModel):
    """One quarter of reported vs. estimated EPS."""

    period: str = Field(..., description='Reporting period label, e.g. "Q1 2025".')
    eps_estimate: float | None = None
    eps_actual: float | None = None
    surprise_pct: float | None = Field(
        default=None,
        description="Percent by which actual beat (or missed, when negative) estimate.",
    )


class EarningsHistory(BaseModel):
    """Compact earnings-history payload for the analyzers."""

    ticker: str
    quarters: list[EarningsQuarter] = Field(
        default_factory=list,
        description="Most-recent quarter last.",
    )
    next_earnings_date: date | None = Field(
        default=None,
        description="Date of the next scheduled earnings release, if known.",
    )


_earnings_cache: dict[str, tuple[float, EarningsHistory]] = {}


def _fetch_earnings_sync(ticker: str) -> EarningsHistory:
    """Sync worker — pull last 4 quarters of beats/misses + next earnings date."""
    handle = yf.Ticker(ticker)
    quarters: list[EarningsQuarter] = []
    next_date: date | None = None

    # earnings_history: DataFrame indexed by quarter, columns include
    # epsEstimate / epsActual / epsDifference / surprisePercent. Schema
    # has drifted across yfinance versions; be defensive about columns.
    try:
        eh = handle.earnings_history
        if eh is not None and not eh.empty:
            for idx, row in eh.tail(4).iterrows():
                period_label = str(idx) if not isinstance(idx, str) else idx
                quarters.append(
                    EarningsQuarter(
                        period=period_label,
                        eps_estimate=_nan_to_none(
                            row.get("epsEstimate") or row.get("EPS Estimate")
                        ),
                        eps_actual=_nan_to_none(row.get("epsActual") or row.get("Reported EPS")),
                        surprise_pct=_nan_to_none(
                            row.get("surprisePercent") or row.get("Surprise(%)")
                        ),
                    )
                )
    except Exception:  # noqa: BLE001
        pass

    try:
        cal = handle.calendar
        if cal is not None:
            raw = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if isinstance(raw, list) and raw:
                first = raw[0]
                if hasattr(first, "date"):
                    next_date = first.date()
                elif isinstance(first, date):
                    next_date = first
    except Exception:  # noqa: BLE001
        pass

    return EarningsHistory(
        ticker=ticker.upper(),
        quarters=quarters,
        next_earnings_date=next_date,
    )


async def get_earnings_history(ticker: str) -> EarningsHistory:
    """Last 4 quarters of EPS beats/misses + next earnings date.

    Cached 1h. Empty payload if yfinance has nothing for this ticker
    (common for small caps or non-US listings).
    """
    key = ticker.upper()
    now = time.time()
    cached = _earnings_cache.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    result = await asyncio.to_thread(_fetch_earnings_sync, ticker)
    _earnings_cache[key] = (now, result)
    return result
