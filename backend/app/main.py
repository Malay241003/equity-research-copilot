"""FastAPI application entry point.

Routes are defined inline here while the surface area is small.
"""

import re
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import settings
from app.llm import LLMProviderError, get_fast_model, get_synthesis_model
from app.rag.chunker import chunk_filing
from app.rag.parser import parse_filing
from app.rag.store import ingest_chunks, search
from app.tools.edgar import fetch_filing_html, list_filings

_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")

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
