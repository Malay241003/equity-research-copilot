"""Token-cost accounting for the research graph.

Phase 3 preview — the goal is to surface a "this report cost $X" badge in
the UI so users (and recruiters) can see the per-run economics. The full
cost dashboard with historical tracking, breakdowns, and rate limits is
Phase 6.

Pricing source: published AWS Bedrock on-demand rates for `us-east-1`,
in USD per 1K tokens. These are the SAME numbers AWS publishes on its
public pricing page — they're not secrets and not user-specific.

**Caveat for the user's setup:** the user runs on AWS Free Tier with
signup credits, so actual billing is $0 until those credits exhaust. The
number we display is "what this run would cost without the credits" —
an honest indicator of production economics, useful for portfolio /
interview talking points.
"""

from typing import Literal

# Per 1K tokens, USD. Update if/when AWS changes published rates.
_PRICING: dict[str, tuple[float, float]] = {
    # (input $/1K, output $/1K)
    "fast": (0.00006, 0.00024),  # Amazon Nova Lite
    "synthesis": (0.0008, 0.0032),  # Amazon Nova Pro
}

# Voyage AI finance-2 embeddings — input only (no "output tokens").
_VOYAGE_INPUT_PER_1K: float = 0.00012


def estimate_llm_cost(
    tier: Literal["fast", "synthesis"],
    usage: dict | None,
) -> float:
    """Cost of a single LLM `.ainvoke()` call, in USD.

    `usage` is the `result.usage_metadata` dict LangChain attaches to the
    AIMessage. It looks like `{"input_tokens": N, "output_tokens": N,
    "total_tokens": N}`. Missing keys default to 0 — a noisy upstream
    shouldn't blow up the graph.
    """
    if not usage:
        return 0.0
    input_rate, output_rate = _PRICING[tier]
    in_tokens = int(usage.get("input_tokens") or 0)
    out_tokens = int(usage.get("output_tokens") or 0)
    return (in_tokens / 1000.0) * input_rate + (out_tokens / 1000.0) * output_rate


def estimate_embedding_cost(tokens: int) -> float:
    """Voyage embedding cost. Used by analyzers' query-embedding calls."""
    return (tokens / 1000.0) * _VOYAGE_INPUT_PER_1K
