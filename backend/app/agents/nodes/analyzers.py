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
        news_block=_format_news(branch_state.get("raw_news")),
        filings_block=_format_chunks(chunks),
    )

    model = get_synthesis_model().with_structured_output(SectionOutput)
    output: SectionOutput = await model.ainvoke(prompt)

    # Trust-but-verify: the LLM filled in `section=` from the schema, but
    # we want to guarantee it matches what the dispatcher sent. If the LLM
    # drifted (e.g. typo'd "valuation" as "evaluation"), pin it back.
    output = output.model_copy(update={"section": section})

    return {
        "analyses": [output],
        "citations": list(output.citations),
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
            },
        )
        for section in sections
    ]
