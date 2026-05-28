"""SSE (Server-Sent Events) translator for the research graph.

Wraps `research_graph.astream(...)` and converts LangGraph's two stream
shapes into one ordered stream of SSE-formatted strings ready to push down
the wire.

LangGraph stream modes used here:

- `"updates"` — yields `{node_name: partial_state_dict}` after each node
  completes. Lets us see "planner just finished, here's the plan",
  "fetch_yfinance just finished, here's the fundamentals", etc.
- `"messages"` — yields `(AIMessageChunk, metadata)` for every LLM token
  produced INSIDE any node. We filter on `metadata["langgraph_node"] ==
  "synthesizer"` so only the synthesizer's tokens reach the client
  (analyzer streams would interleave 5 conversations at once — confusing).

Passing `stream_mode=["updates", "messages"]` makes LangGraph yield
`(mode, chunk)` tuples in the natural order events occur — so we get a
single async iterator that we just walk through and dispatch on `mode`.

Event taxonomy emitted on the wire:

| event       | data shape                                       | when                                                     |
| ----------- | ------------------------------------------------ | -------------------------------------------------------- |
| `phase`     | `{"phase": str, "label": str}`                   | High-level pipeline stage transitions                    |
| `plan`      | `Plan.model_dump()`                              | Planner finished — UI can show company name + focus      |
| `fetched`   | `{"source": str, "summary": str}`                | One fetcher finished — UI can tick off a checkbox        |
| `section`   | `SectionOutput.model_dump()`                     | An analyzer branch finished — UI reveals that section    |
| `synth_token` | `{"text": str}`                                | A token of the synthesizer's prose — UI types it out     |
| `complete`  | `{"report": Report.model_dump()}`                | Whole graph done — UI freezes the streaming display      |
| `error`     | `{"detail": str}`                                | Something blew up; connection about to close             |

All payloads are single-line JSON. SSE format is:

    event: <name>\n
    data: <json>\n
    \n

with the blank trailing line marking message end. EventSource on the
client parses this automatically.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from app.agents.graph import research_graph

# ─── SSE formatting ─────────────────────────────────────────────────


def _sse(event: str, data: dict[str, Any] | None = None) -> str:
    """Format a single SSE message. JSON-encode the payload on one line."""
    payload = json.dumps(data or {}, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _extract_text(content: object) -> str:
    """Pull plain text out of an AIMessageChunk's content field.

    Bedrock's Converse API returns content as a list of typed blocks
    (`[{"type": "text", "text": "..."}, ...]`). Anthropic-direct returns a
    plain string. We normalise both to a string so the SSE token event is
    boringly uniform.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


# ─── Phase labels ───────────────────────────────────────────────────
# Human-readable status strings the UI shows under a spinner. Each one
# corresponds to a high-level pipeline stage (not a 1:1 mapping to nodes —
# the 3 parallel fetchers collapse into one "fetching" phase, etc.).

_PHASE_LABELS = {
    "planning": "Framing the research focus…",
    "fetching": "Pulling market data, filings, and news…",
    "indexing": "Embedding new filings into the vector store…",
    "analyzing": "Analyzing 8 sections in parallel (financials, risks, valuation, macro, sentiment, catalysts…)",
    "synthesizing": "Writing the headline and bottom line…",
    "complete": "Done.",
}


def _phase_event(phase: str) -> str:
    return _sse("phase", {"phase": phase, "label": _PHASE_LABELS.get(phase, phase)})


# ─── Per-node update translators ────────────────────────────────────
# Each translator takes the partial-state dict that the named node
# returned, and yields zero or more SSE strings. Keeping these per-node
# means the dispatcher in `stream_research` stays a flat switch.


def _translate_planner_update(partial: dict[str, Any]) -> list[str]:
    out: list[str] = []
    plan = partial.get("plan")
    if plan is not None:
        # `plan` is a Pydantic Plan — dump it for JSON.
        out.append(_sse("plan", plan.model_dump(mode="json")))
    # Planner done → next phase is fetching.
    out.append(_phase_event("fetching"))
    return out


def _fetcher_summary(node_name: str, partial: dict[str, Any]) -> str:
    """One-line description of what a fetcher returned, for the UI checklist."""
    if node_name == "fetch_yfinance":
        fund = partial.get("raw_fundamentals") or {}
        prices = partial.get("raw_price_history") or {}
        n_points = len(prices.get("points", [])) if prices else 0
        sector = fund.get("sector") or "unknown sector"
        return f"yfinance: {sector}, {n_points} price points"
    if node_name == "fetch_filings":
        filings = partial.get("raw_filings") or []
        return f"EDGAR: {len(filings)} filing(s)"
    if node_name == "fetch_news":
        news = partial.get("raw_news") or []
        return f"news: {len(news)} article(s)"
    return node_name


def _translate_fetcher_update(node_name: str, partial: dict[str, Any]) -> list[str]:
    source = node_name.removeprefix("fetch_")
    return [
        _sse(
            "fetched",
            {"source": source, "summary": _fetcher_summary(node_name, partial)},
        )
    ]


def _translate_indexer_update(partial: dict[str, Any]) -> list[str]:
    newly = partial.get("newly_indexed") or []
    return [
        _sse("indexed", {"newly_indexed_count": len(newly)}),
        _phase_event("analyzing"),
    ]


def _translate_analyzer_update(partial: dict[str, Any]) -> list[str]:
    """The analyze_section node fires once per Send branch. Each invocation
    returns `{"analyses": [one_section_output], "citations": [...]}`. We
    emit one `section` SSE per output so the UI can reveal sections one at
    a time as they finish."""
    out: list[str] = []
    for section in partial.get("analyses") or []:
        out.append(_sse("section", section.model_dump(mode="json")))
    return out


def _translate_synthesizer_update(partial: dict[str, Any]) -> list[str]:
    report = partial.get("report")
    if report is None:
        return []
    return [
        _sse("complete", {"report": report.model_dump(mode="json")}),
        _phase_event("complete"),
    ]


_UPDATE_DISPATCH = {
    "planner": _translate_planner_update,
    "indexer": _translate_indexer_update,
    "analyze_section": _translate_analyzer_update,
    "synthesizer": _translate_synthesizer_update,
}


# ─── Top-level generator ────────────────────────────────────────────


async def stream_research(
    ticker: str,
    query: str | None = None,
) -> AsyncIterator[str]:
    """Async generator yielding SSE strings for one research run.

    Use directly as the body of a FastAPI `StreamingResponse` with
    `media_type="text/event-stream"`.

    Lifecycle:
      1. Yield `phase: planning` immediately so the UI shows a spinner
         instantly (before the first LLM call even starts).
      2. Run `research_graph.astream(initial_state, stream_mode=["updates",
         "messages"])` and translate each yielded item.
      3. On exception, yield a final `error` event before propagating so
         the client gets a clean signal instead of a silent socket drop.
    """
    initial_state: dict[str, Any] = {"ticker": ticker, "query": query, "cost_usd": 0.0}

    # The first thing the client sees — even before any LLM call lands.
    yield _phase_event("planning")

    # Track whether we've already announced the synthesizing phase. The
    # synthesizer's tokens (messages mode) usually arrive before its
    # updates entry, so we use the first synth token as the signal.
    synthesizer_phase_emitted = False

    try:
        async for mode, chunk in research_graph.astream(
            initial_state,
            stream_mode=["updates", "messages"],
        ):
            if mode == "updates":
                # `chunk` is `{node_name: partial_state}` — usually one
                # node per yielded update, but be defensive about more.
                for node_name, partial in chunk.items():
                    if node_name.startswith("fetch_"):
                        for sse in _translate_fetcher_update(node_name, partial):
                            yield sse
                    else:
                        translator = _UPDATE_DISPATCH.get(node_name)
                        if translator is not None:
                            for sse in translator(partial):
                                yield sse

            elif mode == "messages":
                # `chunk` is `(AIMessageChunk, metadata_dict)`.
                msg_chunk, metadata = chunk
                if metadata.get("langgraph_node") != "synthesizer":
                    continue  # ignore analyzer / planner tokens

                if not synthesizer_phase_emitted:
                    yield _phase_event("synthesizing")
                    synthesizer_phase_emitted = True

                text = _extract_text(getattr(msg_chunk, "content", ""))
                if text:
                    yield _sse("synth_token", {"text": text})

    except Exception as exc:  # noqa: BLE001 — surface the real error
        yield _sse(
            "error",
            {"detail": f"{type(exc).__name__}: {exc}"},
        )
        # Don't re-raise — the connection should close cleanly with the
        # error event delivered. FastAPI will treat generator exit as a
        # normal end-of-stream.
