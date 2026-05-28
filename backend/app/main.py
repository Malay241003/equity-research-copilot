"""FastAPI application entry point.

Routes are defined inline here while the surface area is small.
"""

import re
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.graph import research_graph
from app.agents.prompts import load_prompt
from app.agents.state import Report
from app.agents.streaming import stream_research
from app.citation_definitions import MetricDefinition, get_metric_definition
from app.config import settings
from app.llm import LLMProviderError, get_fast_model, get_synthesis_model
from app.rag.chunker import chunk_filing
from app.rag.parser import parse_filing
from app.rag.store import get_chunk_by_id, ingest_chunks, search
from app.tools.edgar import fetch_filing_html, list_filings
from app.tools.yfinance_tool import PriceHistory, get_price_history, get_vix

_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
# SEC accession number: 10 digits, dash, 2 digits, dash, 6 digits.
# Used to recognise a filing chunk_id vs a yfinance metric ID.
_ACCESSION_RE = re.compile(r"\d{10}-\d{2}-\d{6}")

app = FastAPI(title="Equity Research Copilot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Health / debugging endpoints ────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/ping")
async def ping(name: str = "world") -> dict:
    """Trivial echo for connectivity smoke tests."""
    return {"message": f"pong, {name}"}


@app.get("/info")
async def info() -> dict:
    """Non-sensitive snapshot of the current configuration."""
    return {
        "llm_provider": settings.llm_provider,
        "aws_region": settings.aws_region,
        "has_aws_creds": bool(settings.aws_access_key_id and settings.aws_secret_access_key),
        "has_langsmith_key": bool(settings.langsmith_api_key),
        "langsmith_tracing": settings.langchain_tracing_v2,
        "langsmith_project": settings.langsmith_project,
        "cors_origins": settings.cors_origins,
    }


# ─── LLM endpoint ────────────────────────────────────────────────────


class GenerateRequest(BaseModel):
    """Single-shot LLM call request body."""

    prompt: str = Field(..., min_length=1, description="The user prompt.")
    model: Literal["synthesis", "fast"] = Field(
        default="fast",
        description="Which model tier to use. 'fast' = Haiku, 'synthesis' = Sonnet.",
    )


class GenerateResponse(BaseModel):
    """Single-shot LLM call response body."""

    content: str
    model_used: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    """Single-shot LLM call. Hello-world style — confirms Bedrock + LangSmith wiring."""
    try:
        model = get_synthesis_model() if req.model == "synthesis" else get_fast_model()
        result = await model.ainvoke(req.prompt)
    except LLMProviderError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface the underlying error verbatim
        raise HTTPException(
            status_code=502, detail=f"LLM call failed: {type(exc).__name__}: {exc}"
        ) from exc

    # `result.content` is usually a string; for some Bedrock responses it's a list
    # of content blocks. Normalise to string for the response.
    if isinstance(result.content, str):
        content = result.content
    else:
        # Each block has a `text` field for text blocks; concat all text blocks.
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in result.content
        )

    usage = getattr(result, "usage_metadata", None) or {}
    return GenerateResponse(
        content=content,
        model_used=req.model,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
    )


# ─── RAG smoke-test endpoints ────────────────────────────────────────


class FilingSummary(BaseModel):
    accession_number: str
    form: str
    filing_date: date
    sections: int = Field(..., description="Sections parsed from the filing.")
    chunks_added: int = Field(..., description="New chunks committed to Chroma.")


class IngestResponse(BaseModel):
    ticker: str
    chunks_added: int = Field(..., description="Total new chunks across all filings.")
    filings: list[FilingSummary]


class SearchHit(BaseModel):
    text: str
    distance: float = Field(..., description="Cosine distance (smaller = more similar).")
    item_number: str
    section_title: str
    filing_date: str
    accession_number: str


class SearchResponse(BaseModel):
    query: str
    ticker: str
    results: list[SearchHit]


def _validate_ticker(ticker: str) -> str:
    """Uppercase + regex-check a ticker. Raises HTTPException on bad input."""
    ticker_u = ticker.upper()
    if not _TICKER_RE.match(ticker_u):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid ticker {ticker!r}. Must be 1-5 uppercase letters.",
        )
    return ticker_u


@app.post("/ingest/{ticker}", response_model=IngestResponse)
async def ingest_ticker(ticker: str) -> IngestResponse:
    """Fetch the latest 10-K and ingest it into the vector store.

    Idempotent — re-running on a ticker whose filing is already stored is fast
    (no embedding calls). First-time ingestion takes minutes on Voyage free tier.
    """
    ticker_u = _validate_ticker(ticker)

    try:
        filings = await list_filings(ticker_u, form_types=("10-K",), limit=1)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not filings:
        raise HTTPException(status_code=404, detail=f"No 10-K found for {ticker_u}.")

    summaries: list[FilingSummary] = []
    total_added = 0
    for filing in filings:
        html = await fetch_filing_html(filing)
        parsed = parse_filing(html, accession_number=filing.accession_number, form=filing.form)
        chunks = chunk_filing(parsed, ticker=filing.ticker, filing_date=filing.filing_date)
        added = await ingest_chunks(chunks)
        total_added += added
        summaries.append(
            FilingSummary(
                accession_number=filing.accession_number,
                form=filing.form,
                filing_date=filing.filing_date,
                sections=len(parsed.sections),
                chunks_added=added,
            )
        )

    return IngestResponse(ticker=ticker_u, chunks_added=total_added, filings=summaries)


@app.get("/search/{ticker}", response_model=SearchResponse)
async def search_ticker(
    ticker: str,
    q: str,
    k: int = 5,
    item_number: str | None = None,
) -> SearchResponse:
    """Vector-search a ticker's ingested filings.

    Returns the top-k chunks by cosine distance. Optionally narrow to a single
    Item section (e.g. `?item_number=1A` for Risk Factors only).
    """
    ticker_u = _validate_ticker(ticker)

    if not q.strip():
        raise HTTPException(status_code=400, detail="Query string `q` is required.")
    if k < 1 or k > 50:
        raise HTTPException(status_code=400, detail="`k` must be between 1 and 50.")

    results = await search(q, k=k, ticker=ticker_u, item_number=item_number)

    return SearchResponse(
        query=q,
        ticker=ticker_u,
        results=[
            SearchHit(
                text=r.text,
                distance=r.distance,
                item_number=str(r.metadata.get("item_number", "")),
                section_title=str(r.metadata.get("section_title", "")),
                filing_date=str(r.metadata.get("filing_date", "")),
                accession_number=str(r.metadata.get("accession_number", "")),
            )
            for r in results
        ],
    )


# ─── Phase 2: Research graph endpoint ────────────────────────────────


class ResearchRequest(BaseModel):
    """Optional body for /research/{ticker}.

    Send `{}` for a general research run, or `{"query": "..."}` to focus
    the planner on a specific question (e.g. "How exposed is AAPL to
    China supply-chain risk?"). The query is plumbed into the planner's
    `research_focus` and seen by every analyzer.
    """

    query: str | None = Field(
        default=None,
        description="Optional question to focus the research run. Plain prose.",
    )


@app.post("/research/{ticker}", response_model=Report)
async def research_ticker(ticker: str, req: ResearchRequest) -> Report:
    """Run the end-to-end research graph for a ticker.

    Pipeline (see app/agents/graph.py for the topology):
      planner → 3 parallel fetchers → indexer → 5 parallel analyzers → synthesizer

    Latency: ~30–60s on a warm cache (filing already in Chroma). First-time
    ingestion of a fresh ticker can take ~8min due to Voyage free-tier rate
    limits — call POST /ingest/{ticker} first to warm the cache if needed.
    """
    ticker_u = _validate_ticker(ticker)

    initial_state: dict = {"ticker": ticker_u, "query": req.query, "cost_usd": 0.0}

    try:
        final_state = await research_graph.ainvoke(initial_state)
    except Exception as exc:  # noqa: BLE001 — surface the underlying error verbatim
        raise HTTPException(
            status_code=502,
            detail=f"Research graph failed: {type(exc).__name__}: {exc}",
        ) from exc

    report = final_state.get("report")
    if report is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "Graph completed but produced no report. Check server logs — "
                "an analyzer or the synthesizer likely failed silently."
            ),
        )

    return report


# ─── Phase 3: Citation lookup endpoint ───────────────────────────────


class CitationDetail(BaseModel):
    """Detail payload for a single citation, surfaced in the UI side panel.

    The fields are loosely typed because the three source kinds carry very
    different metadata. Frontend dispatches on `source_type` and renders
    only the fields relevant to that kind.
    """

    source_type: Literal["filing", "yfinance", "news"]
    source_id: str

    # Filing-only.
    text: str | None = None
    ticker: str | None = None
    item_number: str | None = None
    section_title: str | None = None
    filing_date: str | None = None
    accession_number: str | None = None

    # yfinance-only.
    metric_name: str | None = None

    # news-only.
    url: str | None = None

    # Educational metadata — populated for yfinance/technical metrics when
    # we have a definition for them. The frontend renders these in the
    # citation popover so the user sees "what does pe_ratio mean?" alongside
    # the value.
    definition: MetricDefinition | None = None

    # Live-data snapshot for metrics that move in real-time (e.g. VIX).
    # Populated on the citation lookup so the popover can show "X.XX as of
    # 2026-05-28" without round-tripping to the report. None for static
    # citations (filings, definitional yfinance fields where the value
    # already appears inline in the section text).
    live_value: float | None = None
    live_as_of: str | None = None


def _classify_source_id(source_id: str) -> Literal["filing", "yfinance", "news"]:
    """Detect what kind of source a citation `source_id` points at.

    Formats in play (cf. Citation model in agents/state.py):
      - filing:   "{ticker}_{accession}_{item}_{chunk_index}"
                  → 2nd `_`-segment matches the SEC accession-number regex.
      - yfinance: "{ticker}_{metric}"          (metric may itself contain `_`)
                  "{ticker}_tech_{indicator}"  (locally-computed technical)
                  "{ticker}_earnings_{period}" (earnings beats/misses)
                  "vix_level"                  (market-wide; no ticker prefix)
      - news:     a URL                        (starts with http(s)://)

    Everything that isn't filing-shaped or a URL is routed to the
    yfinance handler — it owns the educational-metadata lookup table for
    all metric-style IDs (including locally-computed ones like tech_* /
    earnings_* / vix_level).
    """
    if source_id.startswith(("http://", "https://")):
        return "news"
    if _ACCESSION_RE.search(source_id):
        return "filing"
    return "yfinance"


@app.get("/citation/{source_id}", response_model=CitationDetail)
async def get_citation(source_id: str) -> CitationDetail:
    """Resolve a citation `source_id` to a renderable detail payload.

    Used by the UI's citation popover: when the user clicks a `[chunk_id]`
    badge in the report, the frontend fetches this endpoint and renders the
    response in a slide-out side panel.

    Frontend must URL-encode the source_id before calling (news URLs contain
    `/` and `?` — FastAPI auto-decodes the path param on its end).
    """
    kind = _classify_source_id(source_id)

    if kind == "filing":
        chunk = await get_chunk_by_id(source_id)
        if chunk is None:
            raise HTTPException(
                status_code=404,
                detail=f"Filing chunk {source_id!r} not found in the vector store.",
            )
        meta = chunk["metadata"]
        return CitationDetail(
            source_type="filing",
            source_id=source_id,
            text=chunk["text"],
            ticker=meta.get("ticker"),
            item_number=str(meta.get("item_number", "")) or None,
            section_title=meta.get("section_title"),
            filing_date=str(meta.get("filing_date", "")) or None,
            accession_number=meta.get("accession_number"),
        )

    if kind == "yfinance":
        # Special case 1: bare market-wide IDs with no ticker prefix.
        # `vix_level` is the only one today; add to this tuple as new ones
        # land. We look them up directly in the definitions dictionary.
        if source_id == "vix_level":
            # Pull the latest cached VIX so the popover shows the actual
            # current level alongside the definition. Cached 15 min in
            # yfinance_tool.get_vix, so multiple popover opens don't
            # hammer Yahoo.
            vix = await get_vix()
            return CitationDetail(
                source_type="yfinance",
                source_id=source_id,
                ticker=None,
                metric_name=source_id,
                definition=get_metric_definition(source_id),
                live_value=vix.level if vix else None,
                live_as_of=vix.as_of.isoformat() if vix else None,
            )

        # General case: TICKER_metric_name. Split on the FIRST underscore.
        parts = source_id.split("_", 1)
        if len(parts) != 2:
            raise HTTPException(
                status_code=400,
                detail=f"Malformed yfinance source_id: {source_id!r}",
            )
        ticker, metric_name = parts

        # Special case 2: earnings IDs are `{ticker}_earnings_{period}`.
        # The period varies per quarter, so the metric_name is unique per
        # row — but the EDUCATIONAL DEFINITION is the same across rows.
        # Look up the generic "earnings" entry for those.
        lookup_key = "earnings" if metric_name.startswith("earnings") else metric_name

        return CitationDetail(
            source_type="yfinance",
            source_id=source_id,
            ticker=ticker,
            metric_name=metric_name,
            definition=get_metric_definition(lookup_key),
        )

    # news — source_id IS the article URL.
    return CitationDetail(
        source_type="news",
        source_id=source_id,
        url=source_id,
    )


# ─── Phase 3: Price-history endpoint ─────────────────────────────────

_VALID_PERIODS = {"1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max"}


@app.get("/price/{ticker}", response_model=PriceHistory)
async def get_price(ticker: str, period: str = "1y") -> PriceHistory:
    """Daily close-price history for a ticker over a selectable window.

    Period strings match yfinance's vocabulary: `1mo`, `3mo`, `6mo`, `1y`,
    `2y`, `5y`, `10y`, `max`. Cached in-process for 1 hour by
    `yfinance_tool.get_price_history`, so the UI's period-switch buttons
    are effectively free on repeat clicks.

    Exists as a separate endpoint (not just baked into /research) so the
    chart can re-fetch on period change without re-running the whole
    research graph.
    """
    ticker_u = _validate_ticker(ticker)
    if period not in _VALID_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=(f"Invalid period {period!r}. Valid: {sorted(_VALID_PERIODS)}."),
        )

    try:
        return await get_price_history(ticker_u, period=period)
    except Exception as exc:  # noqa: BLE001 — surface the real error
        raise HTTPException(
            status_code=502,
            detail=f"yfinance failed: {type(exc).__name__}: {exc}",
        ) from exc


# ─── Phase 3: Follow-up chat endpoint ────────────────────────────────


class ChatMessage(BaseModel):
    """One turn in the follow-up conversation."""

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)


class ChatRequest(BaseModel):
    """Body for POST /chat/{ticker}.

    Client passes the full `Report` it currently has in state plus the
    prior turns of the conversation. We're deliberately stateless on the
    server — no per-session storage to manage; the client owns the
    conversation history.
    """

    messages: list[ChatMessage] = Field(..., min_length=1)
    report: Report


class ChatResponse(BaseModel):
    """Reply payload."""

    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None


def _report_to_markdown(report: Report) -> str:
    """Render the Report as a readable markdown block for the chat prompt.

    Mirrors what the user sees in the UI: headline, bottom line, then each
    section's summary + key points + cited source IDs. Citations stay as
    `[chunk_id]` tags so the assistant can refer to them too.
    """
    lines: list[str] = [
        f"# {report.headline}",
        "",
        report.bottom_line,
        "",
    ]
    for name, section in report.sections.items():
        title = name.replace("_", " ").title()
        lines.append(f"## {title}")
        lines.append("")
        lines.append(section.summary)
        if section.key_points:
            lines.append("")
            lines.append("Key points:")
            for kp in section.key_points:
                lines.append(f"- {kp}")
        if section.citations:
            lines.append("")
            cite_ids = ", ".join(f"[{c.source_id}]" for c in section.citations)
            lines.append(f"Sources: {cite_ids}")
        lines.append("")
    return "\n".join(lines)


def _format_messages_block(messages: list[ChatMessage]) -> str:
    """Render the conversation history for the prompt.

    `User: …` / `Assistant: …` per turn. The LAST turn is always the user's
    new question — the prompt template tells the LLM to reply to that one.
    """
    return "\n\n".join(
        f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}" for m in messages
    )


@app.post("/chat/{ticker}", response_model=ChatResponse)
async def chat_followup(ticker: str, req: ChatRequest) -> ChatResponse:
    """Answer a follow-up question about an already-generated report.

    Stateless: client sends the report and the message history every turn.
    LLM is Nova Pro (synthesis tier) — chat answers benefit from the same
    quality the synthesizer uses, even though they're short.

    No streaming on this endpoint in v1 — answers are short (1-4 sentences
    typical) and the round-trip on warm Bedrock is ~2-4s. Can be upgraded
    to SSE later if needed.
    """
    ticker_u = _validate_ticker(ticker)
    if req.messages[-1].role != "user":
        raise HTTPException(
            status_code=400,
            detail="The last message must be from the user.",
        )

    prompt = load_prompt("chat").format(
        ticker=ticker_u,
        company_name=req.report.company_name or ticker_u,
        report_markdown=_report_to_markdown(req.report),
        messages_block=_format_messages_block(req.messages),
    )

    try:
        model = get_synthesis_model()
        result = await model.ainvoke(prompt)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Chat LLM call failed: {type(exc).__name__}: {exc}",
        ) from exc

    # Normalise content the same way /generate does — Bedrock sometimes
    # returns a list of content blocks instead of a flat string.
    if isinstance(result.content, str):
        content = result.content
    else:
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in result.content
        )

    usage = getattr(result, "usage_metadata", None) or {}
    return ChatResponse(
        content=content.strip(),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
    )


# ─── Phase 3: Streaming research endpoint ────────────────────────────


@app.get("/research/{ticker}/stream")
async def research_stream(ticker: str, query: str | None = None) -> StreamingResponse:
    """Stream the research graph as Server-Sent Events.

    Same pipeline as `POST /research/{ticker}` but yields progress in real
    time instead of waiting for the full Report. The browser consumes this
    via `new EventSource("/research/AAPL/stream")` — one persistent HTTP
    connection over which the server pushes typed events:

      - `phase`       — high-level stage transitions ("analyzing…")
      - `plan`        — planner output (company name, research focus)
      - `fetched`     — per-fetcher completion
      - `section`     — one analyzer branch finished
      - `synth_token` — a synthesizer prose token
      - `complete`    — the final Report
      - `error`       — graph blew up; connection about to close

    See `app/agents/streaming.py` for the full event taxonomy.

    GET (not POST) because EventSource only speaks GET. Optional research
    focus is passed via `?query=...`.

    Headers worth knowing:
      - `Cache-Control: no-cache` — proxies must not cache SSE.
      - `X-Accel-Buffering: no` — disables nginx response buffering. The
        Phase 6 Railway deploy sits behind nginx-like proxies; without
        this header, events get buffered and the UI looks frozen.
    """
    ticker_u = _validate_ticker(ticker)

    return StreamingResponse(
        stream_research(ticker_u, query),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
