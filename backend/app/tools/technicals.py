"""Derived technical indicators computed from yfinance price history.

We already fetch ~1y of daily closes (`raw_price_history.points`) via
`yfinance_tool.get_price_history`. From those closes we can compute the
indicators traders actually watch — moving averages and rolling-window
momentum — without adding any new data source or paid API.

Kept intentionally small: simple averages, no MACD/RSI/Bollinger. The
goal is to give the sentiment_momentum analyzer real numbers to anchor
on, not to build a TA-Lib clone.

All indicators are tolerant of short histories: if there aren't enough
points, the field is set to None and the analyzer prompt will skip it.
"""

from datetime import date

from app.tools.yfinance_tool import PricePoint

# ─── Window constants ───────────────────────────────────────────────
# Trading-day approximations: 21 ≈ 1 month, 63 ≈ 3 months. These are
# rough but standard — calendar months don't map cleanly to market days
# because of weekends/holidays.

_MONTH_DAYS = 21
_QUARTER_DAYS = 63
_ATR_WINDOW = 14  # Standard period for Average True Range.


def _safe_mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _moving_average(closes: list[float], window: int) -> float | None:
    """Simple moving average of the last `window` closes. None if too few."""
    if len(closes) < window:
        return None
    return _safe_mean(closes[-window:])


def _momentum_pct(closes: list[float], lookback: int) -> float | None:
    """Percent change between the current close and the one `lookback` days ago.
    None if there isn't `lookback + 1` worth of history (`lookback` itself is
    the offset, not the count)."""
    if len(closes) < lookback + 1:
        return None
    past = closes[-lookback - 1]
    current = closes[-1]
    if past == 0:
        return None
    return (current - past) / past * 100.0


def _atr(points: list[PricePoint], window: int = _ATR_WINDOW) -> float | None:
    """Average True Range (Wilder, but using simple mean for transparency).

    True Range for a single day is `max(high - low, |high - prev_close|,
    |low - prev_close|)` — the most permissive of the three to capture
    overnight gaps. ATR is the rolling N-day mean of TR.

    Returns None if fewer than `window + 1` points have both high AND low.
    (`window + 1` because day-1's TR needs day-0's close as a reference.)
    """
    eligible = [p for p in points if p.high is not None and p.low is not None]
    if len(eligible) < window + 1:
        return None

    trs: list[float] = []
    for i in range(1, len(eligible)):
        cur = eligible[i]
        prev_close = eligible[i - 1].close
        # _atr only enters this loop when high/low are non-None.
        assert cur.high is not None and cur.low is not None
        tr = max(
            cur.high - cur.low,
            abs(cur.high - prev_close),
            abs(cur.low - prev_close),
        )
        trs.append(tr)

    if len(trs) < window:
        return None
    return sum(trs[-window:]) / window


def _ytd_momentum_pct(points: list[PricePoint]) -> float | None:
    """Percent change from the first close OF THE CURRENT CALENDAR YEAR.

    Returns None if no point in the history falls in the current year (this
    happens at the very start of January, when the price-history window
    starts in the prior year).
    """
    today = date.today()
    year_points = [p for p in points if p.date.year == today.year]
    if not year_points or len(year_points) < 2:
        return None
    first_close = year_points[0].close
    last_close = year_points[-1].close
    if first_close == 0:
        return None
    return (last_close - first_close) / first_close * 100.0


def compute_technicals(points: list[PricePoint]) -> dict[str, float | str | None]:
    """Compute a dict of technical indicators from a list of PricePoints.

    Returns a small dict with stable keys — None for any indicator that
    didn't have enough data. The analyzer's formatter skips None values
    so the prompt block stays tidy.

    Keys returned (all USD or %):
      - latest_close
      - ma20, ma50, ma200
      - distance_from_ma50_pct   — (close − MA50) / MA50 × 100
      - distance_from_ma200_pct  — (close − MA200) / MA200 × 100
      - momentum_1m_pct, momentum_3m_pct, momentum_ytd_pct
      - trend_signal             — "bullish" / "bearish" / "mixed" / None
                                   from MA20-vs-MA50-vs-MA200 ordering
    """
    if not points:
        return {}

    closes = [p.close for p in points]
    latest = closes[-1]

    ma20 = _moving_average(closes, 20)
    ma50 = _moving_average(closes, 50)
    ma200 = _moving_average(closes, 200)

    def distance(reference: float | None) -> float | None:
        if reference is None or reference == 0:
            return None
        return (latest - reference) / reference * 100.0

    atr = _atr(points)
    atr_pct = (atr / latest * 100.0) if (atr is not None and latest > 0) else None

    return {
        "latest_close": latest,
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "distance_from_ma50_pct": distance(ma50),
        "distance_from_ma200_pct": distance(ma200),
        "momentum_1m_pct": _momentum_pct(closes, _MONTH_DAYS),
        "momentum_3m_pct": _momentum_pct(closes, _QUARTER_DAYS),
        "momentum_ytd_pct": _ytd_momentum_pct(points),
        "trend_signal": _classify_trend(latest, ma20, ma50, ma200),
        # ATR exposed as % of price (normalised volatility) — the
        # absolute ATR dollar number is hard to compare across tickers,
        # but ATR-as-%-of-price is interpretable in isolation.
        "atr_pct": atr_pct,
    }


def _classify_trend(
    close: float,
    ma20: float | None,
    ma50: float | None,
    ma200: float | None,
) -> str | None:
    """Cheap trend classifier from MA ordering.

    Standard chart-reader heuristic:
      - close > MA20 > MA50 > MA200  → bullish stack (price riding the
        averages upward; short-term MA leads long-term MA).
      - close < MA20 < MA50 < MA200  → bearish stack (mirror image).
      - Anything else                → mixed (consolidation, transition,
        or whipsaw).
    Returns None if MAs aren't available.
    """
    if ma20 is None or ma50 is None or ma200 is None:
        return None
    if close > ma20 > ma50 > ma200:
        return "bullish"
    if close < ma20 < ma50 < ma200:
        return "bearish"
    return "mixed"
