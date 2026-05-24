"""Indexer node — feed filings into the vector store.

Wraps Phase 1's ingest pipeline (fetch HTML → parse sections → chunk →
embed → Chroma) and exposes it as one LangGraph node.

Idempotent: re-running on a ticker whose filings are already in Chroma is
near-instant — `ingest_chunks` skips chunk IDs that already exist. First
ingestion of a fresh ticker is slow (~8 min for one 10-K on Voyage's free
tier) because of the embedding rate limit. That's a Phase-1 reality, not
something this node controls.

Reads `state["raw_filings"]` (set by `fetch_filings`). Returns
`newly_indexed` — the list of accession numbers that produced new chunks
on this run. Empty list ⇒ the cache was warm.
"""

from app.agents.state import AgentState
from app.rag.chunker import chunk_filing
from app.rag.parser import parse_filing
from app.rag.store import ingest_chunks
from app.tools.edgar import Filing, fetch_filing_html


async def indexer(state: AgentState) -> dict:
    """Pull filing HTML for each filing in state, parse, chunk, embed, store."""
    raw_filings = state.get("raw_filings") or []
    if not raw_filings:
        # fetch_filings either failed or the ticker has no 10-K. Either way,
        # nothing to index — analyzers will run on yfinance + news only.
        return {"newly_indexed": []}

    # Re-hydrate Filing Pydantic models from the JSON-mode dicts that
    # fetch_filings stored in state. model_validate is the inverse of
    # model_dump(mode="json") — ISO date strings become date objects again.
    filings = [Filing.model_validate(d) for d in raw_filings]

    newly_indexed: list[str] = []
    for filing in filings:
        # Sequential by design: parallel ingest would multiply pressure on
        # Voyage's 3-RPM free-tier rate limit and likely fail. With limit=1
        # in fetch_filings this is moot, but the convention scales safely.
        try:
            html = await fetch_filing_html(filing)
            parsed = parse_filing(
                html,
                accession_number=filing.accession_number,
                form=filing.form,
            )
            chunks = chunk_filing(
                parsed,
                ticker=filing.ticker,
                filing_date=filing.filing_date,
            )
            added = await ingest_chunks(chunks)
        except Exception as exc:  # noqa: BLE001 — keep the graph alive on a per-filing failure
            print(
                f"  [indexer] failed for {filing.accession_number}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            continue

        if added > 0:
            newly_indexed.append(filing.accession_number)
            print(
                f"  [indexer] {filing.ticker} {filing.form} "
                f"{filing.accession_number}: {added} new chunks",
                flush=True,
            )

    return {"newly_indexed": newly_indexed}
