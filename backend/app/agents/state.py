"""AgentState — the typed dictionary that flows through every LangGraph node.

Design rules:

- **One source of truth.** Every node reads from `AgentState` and returns a
  partial dict that LangGraph merges back in. Nodes never mutate state in place.
- **Pydantic everywhere.** Nested structures (`Plan`, `Chunk`, `SectionOutput`,
  `Report`, `Citation`) are typed models. No raw dicts crossing node boundaries.
- **Reducers for parallel writes.** Fields written by parallel branches use
  `Annotated[..., operator.add]` so LangGraph appends instead of overwriting
  when two analyzer branches finish at once. Without this, the second branch
  silently clobbers the first.
- **TypedDict, not BaseModel, for the top-level state.** LangGraph's
  `StateGraph` needs a `TypedDict` so it knows how to merge partial returns.
  Nested fields stay as Pydantic models — best of both worlds.
"""

import operator
from datetime import date
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

# ─── Section taxonomy ───────────────────────────────────────────────
# The five analyzer sections, locked for v1 (per PHASE_NOTES.md decision).
# Using Literal gives us autocomplete + mypy errors if a typo creeps in.

SectionName = Literal[
    "financial_health",
    "growth",
    "risks",
    "competition",
    "valuation",
]

ALL_SECTIONS: tuple[SectionName, ...] = (
    "financial_health",
    "growth",
    "risks",
    "competition",
    "valuation",
)


# ─── Nested Pydantic models ─────────────────────────────────────────


class Plan(BaseModel):
    """The planner node's output — what to research and why."""

    ticker: str = Field(..., description="Uppercase ticker the research targets.")
    company_name: str | None = Field(
        default=None, description="Company display name, filled in once known."
    )
    research_focus: str = Field(
        ...,
        description=(
            "One paragraph describing the research angle — e.g. 'AAPL's services "
            "growth trajectory and AI strategy risk profile'."
        ),
    )
    sections_to_run: list[SectionName] = Field(
        default_factory=lambda: list(ALL_SECTIONS),
        description="Which analyzer sections this plan wants. Defaults to all 5.",
    )


class Citation(BaseModel):
    """A pointer to the source that backs a specific claim."""

    source_type: Literal["filing", "yfinance", "news"] = Field(
        ..., description="Where the claim came from."
    )
    source_id: str = Field(
        ...,
        description=(
            'Stable ID. For filings: "{ticker}_{accession}_{item}_{chunk_index}". '
            'For yfinance: "{ticker}_{metric}". For news: the article URL.'
        ),
    )
    quote: str | None = Field(
        default=None,
        description="Short verbatim snippet, when the source is text-based.",
    )


class Chunk(BaseModel):
    """A single retrieved chunk from the vector store, scoped to a section."""

    chunk_id: str = Field(..., description="Same scheme as Citation.source_id for filings.")
    text: str
    distance: float = Field(..., description="Cosine distance from the section query.")
    item_number: str = Field(..., description="SEC Item number, e.g. '1A', '7'.")
    section_title: str
    accession_number: str
    filing_date: str = Field(
        ..., description="ISO date string; kept as str to survive JSON roundtrips."
    )


class SectionOutput(BaseModel):
    """One analyzer's verdict on its section."""

    section: SectionName
    summary: str = Field(..., description="3-6 sentence write-up of the section.")
    key_points: list[str] = Field(
        default_factory=list, description="Bullet points (≤5) the section turns on."
    )
    citations: list[Citation] = Field(
        default_factory=list, description="At least one citation per material claim."
    )


class Report(BaseModel):
    """The final synthesized report returned to the client."""

    ticker: str
    company_name: str | None = None
    generated_at: date
    headline: str = Field(..., description="One-line investment summary.")
    sections: dict[SectionName, SectionOutput] = Field(
        default_factory=dict,
        description="Per-section outputs, keyed by section name.",
    )
    bottom_line: str = Field(
        ...,
        description=(
            "Synthesizer's overall take: bull case, bear case, and a neutral verdict. "
            "Educational only — not advice."
        ),
    )


class Message(BaseModel):
    """One turn of conversation in follow-up chat (used in Phase 3+)."""

    role: Literal["user", "assistant"]
    content: str


# ─── Top-level graph state ──────────────────────────────────────────


class AgentState(TypedDict, total=False):
    """The dict LangGraph passes through every node.

    `total=False` means every field is optional — at the start of the graph
    only `ticker` is set; later nodes fill in the rest. Each node returns a
    PARTIAL state (just the keys it computed), and LangGraph merges.

    Lists annotated with `operator.add` are reducer-protected: when two
    parallel branches both write to the same list, LangGraph concatenates
    instead of one branch winning. Without this, parallel analyzers would
    silently drop each other's citations.
    """

    # Inputs — always set at graph entry.
    ticker: str
    query: str | None

    # Filled in by the planner.
    plan: Plan | None

    # Filled in by parallel fetchers. Each fetcher writes to a different
    # sub-key, so they don't collide and don't need a reducer.
    raw_price_history: dict | None
    raw_fundamentals: dict | None
    raw_filings: list[dict]
    raw_news: list[dict]

    # Filled in by the indexer. List of accession numbers actually ingested
    # this run (vs. already-cached). Pure bookkeeping for the trace.
    newly_indexed: list[str]

    # Filled in by the analyzer fan-out. Each parallel branch returns one
    # SectionOutput; the reducer appends them into a single list.
    analyses: Annotated[list[SectionOutput], operator.add]

    # Citations accumulate across analyzers too — same reducer story.
    citations: Annotated[list[Citation], operator.add]

    # Filled in by the synthesizer.
    report: Report | None

    # Bookkeeping for tracing and cost-tracking (Phase 6).
    cost_usd: float
    trace_id: str | None

    # Reserved for Phase 3+ follow-up chat.
    messages: Annotated[list[Message], operator.add]
