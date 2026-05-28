"""Analyzer subgraph — five section analysts running in parallel.

Architecture:
- ONE node function (`analyze_section`) instead of five copy-pasted ones.
- A dispatcher (`fan_out_to_analyzers`) returns N `Send` objects, one per
  section. LangGraph runs them concurrently and merges their outputs via
  the `analyses`/`citations` reducers in AgentState.

Why Send API instead of 5 sibling nodes:
- DRY: 1 function, 5 prompts vs. 5 functions, 5 prompts.
- Adding a 6th section later = 1 prompt file + 1 entry in SECTION_QUERIES.
- Each Send carries its OWN branch-local input dict — we can pass exactly
  the data this analyzer needs, no more, no less.

What each analyzer does:
1. Vector-search the filing chunks scoped to its section's question.
2. Build a prompt with formatted yfinance / price / news / chunk blocks,
   each pre-tagged with a citation ID the LLM must echo back.
3. Call Sonnet with `with_structured_output(SectionOutput)`.
4. Return `{"analyses": [out], "citations": out.citations}` — reducers
   append-merge across the 5 branches into the global state.
"""

from langgraph.types import Send

from app.agents.cost import estimate_llm_cost
from app.agents.prompts import load_prompt
from app.agents.state import (
    ALL_SECTIONS,
    AgentState,
    Chunk,
    SectionName,
    SectionOutput,
)
from app.llm import get_synthesis_model
from app.rag.store import search
from app.tools.technicals import compute_technicals
from app.tools.yfinance_tool import PricePoint

# ─── Per-section retrieval queries ─────────────────────────────────
# These are the questions each analyzer fires at Chroma. Keywords, not
# natural sentences — they help the dense retriever surface the right
# filing sections (e.g. "principal risk factors" → Item 1A chunks).

SECTION_QUERIES: dict[SectionName, str] = {
    "financial_health": "balance sheet liquidity debt cash flow operating margins",
    "growth": "revenue growth segment expansion total addressable market new products",
    "risks": "principal risk factors litigation regulatory cybersecurity supply chain",
    "competition": "competitors competitive landscape market share differentiation moat",
    "valuation": "share repurchases dividends capital allocation earnings per share buybacks",
    # Phase 3.5 sections — retrieval queries tuned for what the 10-K
    # typically says about these topics, since news is the other major
    # input and it's pre-attached to the prompt.
    "macro_context": (
        "macroeconomic conditions interest rates inflation consumer demand "
        "foreign currency exchange international trade tariffs"
    ),
    "sentiment_momentum": (
        "stock price performance market capitalization share price "
        "trading volume investor sentiment"
    ),
    "catalysts": (
        "recent material events product launches acquisitions partnerships "
        "litigation outcomes earnings restructuring"
    ),
}

# How many filing chunks to retrieve per analyzer. 6 × 5 = 30 chunks across
# the whole graph run. Each chunk is ~800 tokens, so the analyzer prompts
# stay well under the model's input limit even with overhead.
_TOP_K = 6


# ─── Formatting helpers — turn raw state into prompt-friendly blocks ──
# Every fact in these blocks is prefixed with a citation ID in [brackets].
# The analyzer prompt instructs the LLM: "when you cite a fact, use the
# bracketed ID as the Citation.source_id." This is how we enforce
# "cite or die" without trusting the LLM to invent valid IDs.


def _format_fundamentals(raw: dict | None) -> str:
    """yfinance fundamentals as one [{ticker}_{metric}] line per non-null value."""
    if not raw:
        return "(no fundamentals available — yfinance failed or was rate-limited)"
    ticker = raw.get("ticker", "")
    lines: list[str] = []
    for key in (
        # Core financial metrics (used by financial_health, growth, valuation)
        "market_cap",
        "pe_ratio",
        "forward_pe",
        "price_to_book",
        "profit_margin",
        "operating_margin",
        "revenue_growth",
        "debt_to_equity",
        "sector",
        "industry",
        # Sentiment / momentum / macro context fields (used by the Phase 3.5
        # macro_context, sentiment_momentum, catalysts analyzers — but all
        # 8 analyzers see the same formatted block so the financial ones
        # can reference them too if useful).
        "fifty_two_week_high",
        "fifty_two_week_low",
        "beta",
        "dividend_yield",
        "short_ratio",
        "held_by_institutions",
        "analyst_recommendation",
        "num_analyst_opinions",
    ):
        value = raw.get(key)
        if value is None:
            continue
        lines.append(f"  [{ticker}_{key}] {key}: {value}")
    return "\n".join(lines) or "(all fundamentals were null)"


def _format_price(raw: dict | None) -> str:
    """One-line price summary: first close, last close, % change, point count."""
    if not raw or not raw.get("points"):
        return "(no price history available)"
    points = raw["points"]
    first = points[0]
    last = points[-1]
    pct_change = (last["close"] - first["close"]) / first["close"] * 100 if first["close"] else 0
    return (
        f"  [{raw['ticker']}_price_1y] {raw['period']} from {first['date']} "
        f"to {last['date']}: ${first['close']:.2f} → ${last['close']:.2f} "
        f"({pct_change:+.1f}%), {len(points)} trading days"
    )


def _format_technicals(price_raw: dict | None, ticker: str) -> str:
    """Compute MA20/MA50/MA200 + momentum from price history, format as a
    citation-tagged block. Returns a placeholder if there's no price data
    or fewer than 21 points (not even a month).

    Indicators are computed on the fly per analyzer branch — cheap (a few
    list slices + means), and keeps the fetcher node ignorant of which
    indicators downstream consumers want.
    """
    if not price_raw or not price_raw.get("points"):
        return "(no price history available — technicals not computed)"

    # Re-hydrate PricePoint objects from the dicts we stored in state.
    # Pydantic parses the ISO date string back to a `date` automatically.
    points = [PricePoint.model_validate(p) for p in price_raw["points"]]
    tech = compute_technicals(points)
    if not tech:
        return "(price history too short for technicals)"

    # Each metric becomes its own [{ticker}_tech_{name}] citation ID so the
    # LLM can cite a specific indicator. Skip None values so a short
    # history doesn't pollute the prompt with "(unavailable)" noise.
    lines: list[str] = []
    label_fmt = {
        "latest_close": ("latest close", "${:.2f}"),
        "ma20": ("20-day moving average", "${:.2f}"),
        "ma50": ("50-day moving average", "${:.2f}"),
        "ma200": ("200-day moving average", "${:.2f}"),
        "distance_from_ma50_pct": ("close vs MA50", "{:+.1f}%"),
        "distance_from_ma200_pct": ("close vs MA200", "{:+.1f}%"),
        "momentum_1m_pct": ("1-month momentum", "{:+.1f}%"),
        "momentum_3m_pct": ("3-month momentum", "{:+.1f}%"),
        "momentum_ytd_pct": ("year-to-date momentum", "{:+.1f}%"),
        "trend_signal": (
            "MA-stack trend signal (bullish = close>MA20>MA50>MA200)",
            "{}",
        ),
        "atr_pct": (
            "14-day ATR as % of price (typical daily swing)",
            "{:.2f}%",
        ),
    }
    for key, (label, fmt) in label_fmt.items():
        value = tech.get(key)
        if value is None:
            continue
        formatted = fmt.format(value) if isinstance(value, int | float) else str(value)
        lines.append(f"  [{ticker}_tech_{key}] {label}: {formatted}")
    return "\n".join(lines) or "(no technicals computed)"


def _format_vix(vix_raw: dict | None) -> str:
    """Render the market-wide VIX as a single citable line."""
    if not vix_raw or vix_raw.get("level") is None:
        return "(VIX unavailable — yfinance failed for ^VIX)"
    level = vix_raw["level"]
    as_of = vix_raw.get("as_of", "?")
    # Special citation ID — not company-prefixed because VIX is market-wide.
    return f"  [vix_level] CBOE Volatility Index (^VIX) close as of {as_of}: {level:.2f}"


def _format_earnings(earnings_raw: dict | None, ticker: str) -> str:
    """Format the last 4 quarters of EPS beats/misses + next earnings date."""
    if not earnings_raw:
        return "(no earnings history available)"
    quarters = earnings_raw.get("quarters") or []
    next_date = earnings_raw.get("next_earnings_date")

    lines: list[str] = []
    for q in quarters:
        period = q.get("period", "?")
        est = q.get("eps_estimate")
        actual = q.get("eps_actual")
        surprise = q.get("surprise_pct")
        parts = [f"{period}:"]
        if est is not None:
            parts.append(f"est ${est:.2f}")
        if actual is not None:
            parts.append(f"actual ${actual:.2f}")
        if surprise is not None:
            tag = "beat" if surprise > 0 else "miss" if surprise < 0 else "in-line"
            parts.append(f"({surprise:+.1f}% {tag})")
        lines.append(f"  [{ticker}_earnings_{period.replace(' ', '_')}] {' '.join(parts)}")

    if next_date:
        lines.append(f"  [{ticker}_earnings_next] Next earnings expected: {next_date}")

    return "\n".join(lines) or "(no earnings data returned by yfinance)"


def _format_news(raw: list[dict] | None) -> str:
    """News titles + one-line descriptions, prefixed by URL as cite ID."""
    if not raw:
        return "(no recent news)"
    lines: list[str] = []
    for article in raw[:10]:  # cap at 10 to bound prompt length
        url = article.get("url") or "(no url)"
        title = article.get("title", "(no title)")
        desc = article.get("description") or ""
        lines.append(f"  [{url}] {title}")
        if desc:
            lines.append(f"    {desc}")
    return "\n".join(lines)


def _format_chunks(chunks: list[Chunk]) -> str:
    """Filing chunks block. Each chunk prefixed with its chunk_id for citing."""
    if not chunks:
        return "(no filing chunks retrieved)"
    blocks: list[str] = []
    for c in chunks:
        header = (
            f"  [{c.chunk_id}] (Item {c.item_number}, {c.section_title}, "
            f"filed {c.filing_date}, distance={c.distance:.2f})"
        )
        blocks.append(f"{header}\n    {c.text}")
    return "\n\n".join(blocks)


# ─── Retrieval ─────────────────────────────────────────────────────


async def _retrieve_chunks(ticker: str, section: SectionName) -> list[Chunk]:
    """Vector-search the filings for this section's question, scoped to ticker."""
    query = SECTION_QUERIES[section]
    results = await search(query=query, k=_TOP_K, ticker=ticker)

    chunks: list[Chunk] = []
    for r in results:
        meta = r.metadata
        chunk_id = (
            f"{meta.get('ticker', ticker)}_{meta.get('accession_number', '?')}_"
            f"{meta.get('item_number', '?')}_{meta.get('chunk_index', '?')}"
        )
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                text=r.text,
                distance=r.distance,
                item_number=str(meta.get("item_number", "")),
                section_title=str(meta.get("section_title", "")),
                accession_number=str(meta.get("accession_number", "")),
                filing_date=str(meta.get("filing_date", "")),
            )
        )
    return chunks


# ─── The analyzer node ─────────────────────────────────────────────


async def analyze_section(branch_state: dict) -> dict:
    """One analyzer invocation. Receives a branch-local dict from `Send`,
    returns a partial update to the global AgentState.

    `branch_state` is whatever the dispatcher packaged in its Send — NOT
    the global AgentState. That's the Send API's design: each parallel
    branch can have its own input shape.

    The return value `{"analyses": [...], "citations": [...]}` is then
    merged into the global state by the `operator.add` reducers on those
    fields — so the 5 parallel branches accumulate without clobbering.
    """
    section: SectionName = branch_state["section"]
    ticker: str = branch_state["ticker"]
    plan = branch_state.get("plan")

    chunks = await _retrieve_chunks(ticker, section)

    prompt = load_prompt(f"analyzers/{section}").format(
        ticker=ticker,
        company_name=(plan.company_name if plan else None) or ticker,
        research_focus=(plan.research_focus if plan else "(no plan)"),
        fundamentals_block=_format_fundamentals(branch_state.get("raw_fundamentals")),
        price_block=_format_price(branch_state.get("raw_price_history")),
        # technicals_block / vix_block / earnings_block are only referenced
        # by specific prompts (sentiment_momentum, macro_context,
        # financial_health/growth respectively), but str.format() silently
        # ignores unused keys — safe to pass to every analyzer so adding
        # consumers later is a one-prompt change rather than touching here.
        technicals_block=_format_technicals(branch_state.get("raw_price_history"), ticker),
        vix_block=_format_vix(branch_state.get("raw_vix")),
        earnings_block=_format_earnings(branch_state.get("raw_earnings"), ticker),
        news_block=_format_news(branch_state.get("raw_news")),
        filings_block=_format_chunks(chunks),
    )

    # include_raw=True returns {"raw": AIMessage, "parsed": SectionOutput}
    # so we can grab the underlying token usage for cost tracking. Without it
    # the AIMessage is consumed internally and usage_metadata is lost.
    model = get_synthesis_model().with_structured_output(SectionOutput, include_raw=True)

    # Bedrock's ConverseStream is the path LangGraph uses when the parent
    # graph is run with stream_mode="messages" (we want that on for the
    # synthesizer). It occasionally produces "Model produced invalid
    # sequence as part of ToolUse" errors — a Bedrock-side flake, not
    # something we control. Retry once; if it still fails, return a
    # placeholder so the synthesizer can still assemble a partial report
    # rather than the whole graph dying on one bad branch.
    output: SectionOutput
    cost: float
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            response = await model.ainvoke(prompt)
            output = response["parsed"]
            cost = estimate_llm_cost("synthesis", getattr(response["raw"], "usage_metadata", None))
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001 — surface to the placeholder path
            last_exc = exc
            print(
                f"  [analyze_section:{section}] attempt {attempt + 1} failed: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )

    if last_exc is not None:
        # Both attempts blew up. Emit a degraded SectionOutput so the
        # report still renders with a clear "this section failed" message
        # rather than the entire stream dying. No citations on a failed
        # section — the LLM never produced any.
        print(
            f"  [analyze_section:{section}] giving up after retry; returning placeholder",
            flush=True,
        )
        output = SectionOutput(
            section=section,
            summary=(
                f"This section failed to generate due to a model streaming error "
                f"({type(last_exc).__name__}). The other sections completed normally. "
                f"Re-run the report to retry — the failure is transient."
            ),
            key_points=[],
            citations=[],
        )
        cost = 0.0

    # Trust-but-verify: the LLM filled in `section=` from the schema, but
    # we want to guarantee it matches what the dispatcher sent. If the LLM
    # drifted (e.g. typo'd "valuation" as "evaluation"), pin it back.
    output = output.model_copy(update={"section": section})

    return {
        "analyses": [output],
        "citations": list(output.citations),
        "cost_usd": cost,
    }


# ─── Fan-out dispatcher ────────────────────────────────────────────


def fan_out_to_analyzers(state: AgentState) -> list[Send]:
    """Conditional-edge function that fans the indexer out to N analyzers.

    Returns a list of `Send(node_name, payload)` objects — one per section
    we want to run. LangGraph receives this list and starts that many
    parallel invocations of the named node, each with its own payload.

    Note: each Send's payload is the EXACT input the analyzer will receive.
    We pre-extract the keys analyze_section needs so the branch state
    stays small (faster checkpointing, cleaner LangSmith trace).
    """
    sections = (
        state["plan"].sections_to_run if state.get("plan") is not None else list(ALL_SECTIONS)
    )

    return [
        Send(
            "analyze_section",
            {
                "section": section,
                "ticker": state["ticker"],
                "plan": state.get("plan"),
                "raw_fundamentals": state.get("raw_fundamentals"),
                "raw_price_history": state.get("raw_price_history"),
                "raw_news": state.get("raw_news"),
                "raw_vix": state.get("raw_vix"),
                "raw_earnings": state.get("raw_earnings"),
            },
        )
        for section in sections
    ]
