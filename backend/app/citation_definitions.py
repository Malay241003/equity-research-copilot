"""Educational definitions for citation source IDs.

When the user clicks a citation badge in the report, the side panel shows
the raw value AND a plain-English explanation of what the metric means
plus the typical interpretation thresholds. This module is the single
source of truth for those explanations.

Threshold tuples are `(min, max, label)` — `None` means open-ended on that
side. The frontend renders these as a "Reading the number" table.

These are general-purpose heuristics. Sector context matters a lot for
multiples (a 40x P/E is normal for hyper-growth software, extreme for
utilities), so each definition can carry a `note` that flags the caveat.
"""

from pydantic import BaseModel, Field


class Threshold(BaseModel):
    """One band of the interpretation table."""

    min: float | None = Field(
        default=None, description="Lower bound (inclusive), None = open-ended."
    )
    max: float | None = Field(
        default=None, description="Upper bound (exclusive), None = open-ended."
    )
    label: str = Field(..., description="What this range typically signals.")


class MetricDefinition(BaseModel):
    """Educational metadata for a single citation source ID."""

    name: str = Field(..., description="Human-readable name (e.g. 'Trailing P/E Ratio').")
    definition: str = Field(..., description="1-3 sentence plain-English explanation.")
    unit: str | None = Field(default=None, description='Display unit (e.g. "%", "$", "ratio").')
    thresholds: list[Threshold] = Field(
        default_factory=list,
        description="Interpretation bands, ordered low → high.",
    )
    note: str | None = Field(
        default=None,
        description="Caveat or context (sector-dependence, edge cases).",
    )


# ─── The dictionary ─────────────────────────────────────────────────
# Keys are the "metric_name" portion of the yfinance source_id format
# (TICKER_metric_name). For technical indicators emitted by
# `_format_technicals`, the metric_name is `tech_<key>`; those entries
# are prefixed with `tech_` below.

_DEFS: dict[str, MetricDefinition] = {
    # ─── Valuation multiples ────────────────────────────────────────
    "pe_ratio": MetricDefinition(
        name="Trailing P/E Ratio",
        definition=(
            "Price-to-earnings ratio: the stock price divided by earnings per "
            "share over the last 12 months. Tells you how much investors are "
            "paying for each dollar of past profit."
        ),
        unit="ratio",
        thresholds=[
            Threshold(min=None, max=10, label="Deep value (or signal of earnings concern)"),
            Threshold(min=10, max=20, label="Reasonable for a mature business"),
            Threshold(min=20, max=40, label="Growth premium"),
            Threshold(min=40, max=None, label="Expensive — needs strong forward growth to justify"),
        ],
        note="Highly sector-dependent: 40x is normal for high-growth software but extreme for utilities or banks.",
    ),
    "forward_pe": MetricDefinition(
        name="Forward P/E Ratio",
        definition=(
            "Same as P/E but using analyst estimates of next-12-months earnings. "
            "Lower than trailing P/E means earnings are expected to grow."
        ),
        unit="ratio",
        thresholds=[
            Threshold(min=None, max=10, label="Cheap on forward estimates"),
            Threshold(min=10, max=20, label="Fairly priced"),
            Threshold(min=20, max=40, label="Priced for growth"),
            Threshold(min=40, max=None, label="Steep premium even on optimistic estimates"),
        ],
        note="If forward < trailing, the market expects earnings to grow; if forward > trailing, decline is expected.",
    ),
    "price_to_book": MetricDefinition(
        name="Price-to-Book Ratio",
        definition=(
            "Stock price divided by book value (assets minus liabilities) per share. "
            "Tells you how much you're paying relative to the company's accounting net worth."
        ),
        unit="ratio",
        thresholds=[
            Threshold(min=None, max=1, label="Trading below book value (value or distress signal)"),
            Threshold(min=1, max=3, label="Normal range"),
            Threshold(min=3, max=10, label="Premium to book — common for asset-light businesses"),
            Threshold(min=10, max=None, label="Very high — typical for software / IP-heavy"),
        ],
        note="Less meaningful for tech and IP-heavy companies (book value undercounts intangible assets).",
    ),
    "market_cap": MetricDefinition(
        name="Market Capitalization",
        definition=(
            "Total dollar value of the company's outstanding shares (shares × price). "
            "Often used to categorise companies as small / mid / large / mega cap."
        ),
        unit="$",
        thresholds=[
            Threshold(min=None, max=2_000_000_000, label="Small cap (<$2B)"),
            Threshold(min=2_000_000_000, max=10_000_000_000, label="Mid cap ($2B–$10B)"),
            Threshold(min=10_000_000_000, max=200_000_000_000, label="Large cap ($10B–$200B)"),
            Threshold(min=200_000_000_000, max=None, label="Mega cap (>$200B)"),
        ],
    ),
    # ─── Profitability ──────────────────────────────────────────────
    "profit_margin": MetricDefinition(
        name="Net Profit Margin",
        definition=(
            "Net income as a percentage of revenue. The cents of profit kept "
            "from each dollar of sales, after all expenses, interest, and taxes."
        ),
        unit="%",
        thresholds=[
            Threshold(min=None, max=0, label="Unprofitable"),
            Threshold(min=0, max=5, label="Thin margins (commodity-like)"),
            Threshold(min=5, max=15, label="Healthy"),
            Threshold(min=15, max=30, label="Strong"),
            Threshold(min=30, max=None, label="Exceptional (software / luxury / monopoly traits)"),
        ],
        note="yfinance reports this as a decimal (0.27 = 27%). The UI shows it as %.",
    ),
    "operating_margin": MetricDefinition(
        name="Operating Margin",
        definition=(
            "Operating income (revenue minus cost of goods + operating expenses) "
            "as a percentage of revenue. Strips out tax and interest effects, so "
            "it's a cleaner read on core business profitability than net margin."
        ),
        unit="%",
        thresholds=[
            Threshold(min=None, max=0, label="Losing money at the operating level"),
            Threshold(min=0, max=10, label="Thin"),
            Threshold(min=10, max=25, label="Healthy"),
            Threshold(min=25, max=40, label="Strong"),
            Threshold(min=40, max=None, label="Best-in-class"),
        ],
    ),
    "revenue_growth": MetricDefinition(
        name="Revenue Growth (YoY)",
        definition=(
            "Percentage change in revenue vs. the same quarter last year. "
            "The primary signal of top-line momentum."
        ),
        unit="%",
        thresholds=[
            Threshold(min=None, max=0, label="Revenue declining"),
            Threshold(min=0, max=5, label="Mature / cyclical low"),
            Threshold(min=5, max=15, label="Steady growth"),
            Threshold(min=15, max=30, label="Strong growth"),
            Threshold(min=30, max=None, label="Hyper-growth (often early-stage)"),
        ],
    ),
    # ─── Balance sheet ──────────────────────────────────────────────
    "debt_to_equity": MetricDefinition(
        name="Debt-to-Equity Ratio",
        definition=(
            "Total debt divided by shareholder equity. A leverage gauge — "
            "higher means more of the business is financed with borrowing "
            "rather than owner capital."
        ),
        unit="ratio",
        thresholds=[
            Threshold(min=None, max=0.5, label="Conservative / low leverage"),
            Threshold(min=0.5, max=1.0, label="Moderate leverage"),
            Threshold(min=1.0, max=2.0, label="Levered"),
            Threshold(min=2.0, max=None, label="Heavily levered (financial-stress risk)"),
        ],
        note="yfinance often reports this as a percentage (79.55 = 0.7955). Read accordingly.",
    ),
    # ─── Positioning / sentiment ────────────────────────────────────
    "beta": MetricDefinition(
        name="Beta (5Y)",
        definition=(
            "How volatile the stock has been vs. the broader market over ~5 years. "
            "Beta of 1.0 means the stock moves in line with the market."
        ),
        unit="ratio",
        thresholds=[
            Threshold(min=None, max=0.5, label="Defensive (moves less than market)"),
            Threshold(min=0.5, max=0.8, label="Lower-volatility"),
            Threshold(min=0.8, max=1.2, label="Roughly market-like"),
            Threshold(min=1.2, max=1.8, label="More cyclical / amplifies market moves"),
            Threshold(min=1.8, max=None, label="High-beta (large amplification of market swings)"),
        ],
    ),
    "dividend_yield": MetricDefinition(
        name="Dividend Yield",
        definition=(
            "Annual dividend per share divided by current price. The cash income "
            "you'd get per dollar invested if the dividend stays flat."
        ),
        unit="%",
        thresholds=[
            Threshold(min=None, max=1, label="Low — growth-orientation (reinvesting profits)"),
            Threshold(min=1, max=3, label="Modest income"),
            Threshold(min=3, max=5, label="Solid income"),
            Threshold(
                min=5, max=None, label="High — verify it's not a yield trap (declining price)"
            ),
        ],
        note="A very high yield often signals distress — the price has fallen, lifting the yield mechanically.",
    ),
    "short_ratio": MetricDefinition(
        name="Short Ratio (Days-to-Cover)",
        definition=(
            "Total shares sold short divided by the average daily volume. The "
            "number of trading days it would take short sellers to buy back "
            "their positions at typical volume."
        ),
        unit="days",
        thresholds=[
            Threshold(min=None, max=2, label="Low short interest"),
            Threshold(min=2, max=5, label="Moderate"),
            Threshold(min=5, max=10, label="High — meaningful skepticism"),
            Threshold(min=10, max=None, label="Very high — potential short-squeeze candidate"),
        ],
    ),
    "held_by_institutions": MetricDefinition(
        name="Institutional Ownership %",
        definition=(
            "Fraction of the float held by mutual funds, pensions, ETFs, and "
            "other institutional investors. Higher = more pro-investor scrutiny "
            "and typically less retail-driven price swings."
        ),
        unit="%",
        thresholds=[
            Threshold(min=None, max=0.3, label="Retail-dominated"),
            Threshold(min=0.3, max=0.7, label="Mixed ownership"),
            Threshold(min=0.7, max=0.95, label="Institution-heavy (typical large cap)"),
            Threshold(min=0.95, max=None, label="Near-total institutional (very low float)"),
        ],
        note="yfinance reports as a decimal (0.86 = 86%).",
    ),
    "analyst_recommendation": MetricDefinition(
        name="Analyst Consensus Rating",
        definition=(
            "Aggregated tier label from yfinance summarising Wall Street analyst "
            'opinions. Possible values: "strong_buy", "buy", "hold", "sell", "strong_sell".'
        ),
        note="Consensus ratings are slow-moving and biased toward buy; treat as one input, not a decisive signal.",
    ),
    "num_analyst_opinions": MetricDefinition(
        name="Number of Analyst Opinions",
        definition=(
            "How many sell-side analysts contribute to the consensus rating. "
            "More analysts = more stable consensus; fewer = sample-size caveat."
        ),
        thresholds=[
            Threshold(min=None, max=5, label="Sparse coverage"),
            Threshold(min=5, max=15, label="Decent coverage"),
            Threshold(min=15, max=None, label="Heavy coverage (mega-cap typical)"),
        ],
    ),
    "fifty_two_week_high": MetricDefinition(
        name="52-Week High",
        definition="Highest closing price over the past year. Pure price-range context.",
        unit="$",
    ),
    "fifty_two_week_low": MetricDefinition(
        name="52-Week Low",
        definition="Lowest closing price over the past year. Pure price-range context.",
        unit="$",
    ),
    "price_1y": MetricDefinition(
        name="1-Year Price Path",
        definition=(
            "The full daily-close series used to compute moving averages, "
            "momentum, and the chart at the top of the report."
        ),
    ),
    # ─── Technical indicators (prefix `tech_`) ──────────────────────
    "tech_latest_close": MetricDefinition(
        name="Latest Close",
        definition="Most recent daily closing price in the fetched 1-year series.",
        unit="$",
    ),
    "tech_ma20": MetricDefinition(
        name="20-Day Moving Average",
        definition=(
            "Simple average of the last 20 daily closes. A short-term trend "
            "reference — price above the MA20 typically means short-term uptrend."
        ),
        unit="$",
    ),
    "tech_ma50": MetricDefinition(
        name="50-Day Moving Average",
        definition=(
            "Simple average of the last 50 daily closes. The most-watched "
            "intermediate-trend reference among traders."
        ),
        unit="$",
    ),
    "tech_ma200": MetricDefinition(
        name="200-Day Moving Average",
        definition=(
            "Simple average of the last 200 daily closes. The long-term trend "
            "reference; price above MA200 is widely treated as a structural uptrend."
        ),
        unit="$",
    ),
    "tech_distance_from_ma50_pct": MetricDefinition(
        name="Distance from MA50",
        definition=(
            "Percent that the latest close sits above (positive) or below "
            "(negative) the 50-day moving average."
        ),
        unit="%",
        thresholds=[
            Threshold(min=None, max=-10, label="Stretched below MA50 — oversold"),
            Threshold(min=-10, max=-2, label="Below MA50 — short-term weakness"),
            Threshold(min=-2, max=2, label="Right at the MA50 — flat trend"),
            Threshold(min=2, max=10, label="Above MA50 — short-term strength"),
            Threshold(min=10, max=None, label="Stretched above MA50 — short-term overbought"),
        ],
    ),
    "tech_distance_from_ma200_pct": MetricDefinition(
        name="Distance from MA200",
        definition=(
            "Percent that the latest close sits above (positive) or below "
            "(negative) the 200-day moving average. The cleanest single number "
            "for long-term trend strength."
        ),
        unit="%",
        thresholds=[
            Threshold(min=None, max=-15, label="Deep below MA200 — long-term downtrend"),
            Threshold(min=-15, max=-5, label="Below MA200 — long-term weak"),
            Threshold(min=-5, max=5, label="At MA200 — trend pivot zone"),
            Threshold(min=5, max=20, label="Above MA200 — long-term uptrend"),
            Threshold(min=20, max=None, label="Stretched above MA200 — risk of mean reversion"),
        ],
    ),
    "tech_momentum_1m_pct": MetricDefinition(
        name="1-Month Momentum",
        definition="Percent change over the last ~21 trading days.",
        unit="%",
        thresholds=[
            Threshold(min=None, max=-10, label="Sharp 1M selloff"),
            Threshold(min=-10, max=-3, label="Mild 1M weakness"),
            Threshold(min=-3, max=3, label="Flat"),
            Threshold(min=3, max=10, label="Mild 1M strength"),
            Threshold(min=10, max=None, label="Strong 1M move (verify sustainability)"),
        ],
    ),
    "tech_momentum_3m_pct": MetricDefinition(
        name="3-Month Momentum",
        definition="Percent change over the last ~63 trading days. Smooths noise vs. 1M momentum.",
        unit="%",
        thresholds=[
            Threshold(min=None, max=-15, label="Quarter of decline"),
            Threshold(min=-15, max=-5, label="Slight quarterly weakness"),
            Threshold(min=-5, max=5, label="Flat quarter"),
            Threshold(min=5, max=15, label="Solid quarterly advance"),
            Threshold(min=15, max=None, label="Strong quarter — momentum regime"),
        ],
    ),
    "tech_momentum_ytd_pct": MetricDefinition(
        name="Year-to-Date Momentum",
        definition="Percent change from the first trading day of the current calendar year to today.",
        unit="%",
    ),
    "tech_trend_signal": MetricDefinition(
        name="MA-Stack Trend Signal",
        definition=(
            "Cheap chart-reader heuristic from the ordering of close, MA20, MA50, MA200. "
            '"bullish" = close > MA20 > MA50 > MA200 (short-term lead, all stacked up). '
            '"bearish" = the mirror. "mixed" = anything else (consolidation/transition).'
        ),
        note="Not a buy/sell signal — it summarises trend posture across timeframes in one word.",
    ),
    "tech_atr_pct": MetricDefinition(
        name="ATR as % of Price (14-day)",
        definition=(
            "Average True Range normalised by current price. ATR measures the "
            "typical daily price range (the average of the daily high-minus-low, "
            "adjusted for overnight gaps). Divided by price, it tells you the "
            "stock's typical daily swing as a percentage."
        ),
        unit="%",
        thresholds=[
            Threshold(min=None, max=1, label="Low volatility (utility-like)"),
            Threshold(min=1, max=2, label="Normal large-cap volatility"),
            Threshold(min=2, max=4, label="Elevated (small caps, hot growth names)"),
            Threshold(min=4, max=None, label="High volatility — sizing matters"),
        ],
    ),
    "earnings": MetricDefinition(
        name="Quarterly Earnings (EPS Beat / Miss)",
        definition=(
            "One quarter of reported earnings vs. the consensus analyst estimate. "
            "The 'surprise' percentage measures how far actual EPS came in above "
            "(beat) or below (miss) what analysts forecast. Trajectory across "
            "quarters matters more than any single number."
        ),
        unit="%",
        thresholds=[
            Threshold(min=None, max=-5, label="Notable miss — typically punished by the market"),
            Threshold(min=-5, max=-1, label="Mild miss"),
            Threshold(min=-1, max=1, label="In-line"),
            Threshold(min=1, max=10, label="Healthy beat"),
            Threshold(
                min=10,
                max=None,
                label="Major beat — strong signal of momentum or low-balled guidance",
            ),
        ],
        note=(
            "Surprise % alone is misleading. A 'beat' against a sandbagged guide "
            "is less meaningful than a small beat against a high bar. Read the "
            "TREND across the last 4 quarters: accelerating beats = growth "
            "regime; shrinking beats = decelerating."
        ),
    ),
    "vix_level": MetricDefinition(
        name="VIX (Market Fear Index)",
        definition=(
            "CBOE Volatility Index — the market's expected 30-day volatility "
            "implied by S&P 500 option prices. Widely used as a 'fear gauge': "
            "rises when the market expects turbulence, falls when calm."
        ),
        unit="level",
        thresholds=[
            Threshold(min=None, max=15, label="Complacent / low-volatility regime"),
            Threshold(min=15, max=20, label="Normal"),
            Threshold(min=20, max=30, label="Elevated stress"),
            Threshold(min=30, max=None, label="High fear — panic / crash conditions"),
        ],
        note="VIX is market-wide, not company-specific — the same value applies to every stock on a given day.",
    ),
}


def get_metric_definition(metric_name: str) -> MetricDefinition | None:
    """Look up the educational metadata for a metric name. None if unknown."""
    return _DEFS.get(metric_name)
