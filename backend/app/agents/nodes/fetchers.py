"""Parallel fetcher nodes — the I/O layer of the graph.

Three nodes run in parallel right after the planner:

- `fetch_yfinance` — price history + fundamentals (two yfinance calls run
  in parallel via `asyncio.gather`; one node call total).
- `fetch_filings`  — SEC EDGAR filings list (metadata only — the indexer
  fetches the HTML and embeds chunks in the next step).
- `fetch_news`     — NewsAPI articles for the ticker.

LangGraph runs them in parallel because they each have an edge from the
planner. They each write to a different `AgentState` sub-key, so there's
no collision and we don't need a reducer here.

Error policy: each fetcher catches broad exceptions, prints a one-liner,
and returns empty data. The graph keeps running. The synthesizer treats
missing data as "I have no information about X" rather than crashing. This
is the right default for an agentic system — the user wants a partial
report over a hard 500.
"""

import asyncio

from app.agents.state import AgentState
from app.tools.edgar import list_filings
from app.tools.newsapi import NewsAPIError, get_news
from app.tools.yfinance_tool import get_fundamentals, get_price_history


async def fetch_yfinance(state: AgentState) -> dict:
    """Fetch 1-year price history and fundamentals from yfinance in parallel.

    Backfills `plan.company_name` if the planner left it null — analyzer
    prompts read the human-readable name, not just the ticker.
    """
    ticker = state["ticker"]

    try:
        # asyncio.gather runs both coroutines concurrently and waits for both.
        # yfinance is scrapy-style sync inside, but each call is wrapped in
        # asyncio.to_thread, so they really do go in parallel on worker threads.
        price, fundamentals = await asyncio.gather(
            get_price_history(ticker, period="1y"),
            get_fundamentals(ticker),
        )
    except Exception as exc:  # noqa: BLE001 — yfinance can throw arbitrary errors
        print(f"  [fetch_yfinance] failed: {type(exc).__name__}: {exc}", flush=True)
        return {"raw_price_history": None, "raw_fundamentals": None}

    update: dict = {
        # mode="json" so date objects serialize to ISO strings — survive
        # LangGraph's state checkpointing without custom encoders.
        "raw_price_history": price.model_dump(mode="json"),
        "raw_fundamentals": fundamentals.model_dump(mode="json"),
    }

    # Backfill company_name on the Plan if we just learned it. Pydantic
    # models are immutable-by-convention; `model_copy(update=…)` gives us
    # a new Plan with the one field changed. Returning {"plan": new_plan}
    # asks LangGraph to replace the plan in state — safe because no other
    # parallel branch writes to plan.
    plan = state.get("plan")
    if plan is not None and plan.company_name is None and fundamentals.name:
        update["plan"] = plan.model_copy(update={"company_name": fundamentals.name})

    return update


async def fetch_filings(state: AgentState) -> dict:
    """Fetch the latest 10-K filing metadata from SEC EDGAR.

    Only metadata — the indexer pulls the HTML and embeds chunks next.
    Limiting to the latest 1 10-K matches Phase 1's `/ingest` endpoint;
    we can broaden to 10-Q + multi-year in later phases.
    """
    ticker = state["ticker"]

    try:
        filings = await list_filings(ticker, form_types=("10-K",), limit=1)
    except ValueError as exc:
        # Ticker not registered with SEC — known failure mode.
        print(f"  [fetch_filings] ticker not in SEC: {exc}", flush=True)
        return {"raw_filings": []}
    except Exception as exc:  # noqa: BLE001 — SEC HTTP errors land here
        print(f"  [fetch_filings] failed: {type(exc).__name__}: {exc}", flush=True)
        return {"raw_filings": []}

    return {"raw_filings": [f.model_dump(mode="json") for f in filings]}


async def fetch_news(state: AgentState) -> dict:
    """Fetch recent news via NewsAPI for the ticker.

    Uses ticker-only search for v1 so this node stays fully independent of
    the yfinance branch — all three fetchers can then run truly in parallel.
    Company-name-aware search is a Phase 2.5 upgrade (would require waiting
    for fundamentals.name from fetch_yfinance).
    """
    ticker = state["ticker"]

    try:
        articles = await get_news(ticker)
    except NewsAPIError as exc:
        # Missing key, rate limited, NewsAPI returned status=error. All recoverable.
        print(f"  [fetch_news] {exc}", flush=True)
        return {"raw_news": []}
    except Exception as exc:  # noqa: BLE001 — defensive catch for unknown HTTP errors
        print(f"  [fetch_news] failed: {type(exc).__name__}: {exc}", flush=True)
        return {"raw_news": []}

    return {"raw_news": [a.model_dump(mode="json") for a in articles]}
