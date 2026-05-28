"""Synthesizer node — assembles the final Report.

This is the fan-in counterpart to the analyzer fan-out: 5 SectionOutputs go
in, 1 Report comes out.

Design choices:

1. **No regeneration of section content.** Each SectionOutput already carries
   the analyst's summary, key points, and citations — replaying those through
   Nova Pro would (a) burn tokens and (b) invite drift from what the analyst
   actually said. The synthesizer's job is the report-level prose only: a
   one-line `headline` and a 4–6 sentence `bottom_line`.

2. **Plain text output, not structured output.** Phase 2 used
   `with_structured_output(SynthesizerOutput)`, which wraps the LLM in a
   tool-calling pattern. That works fine for batch use but streams as
   incremental JSON tool-call fragments — ugly to render token-by-token in
   the UI. For Phase 3 we ask the LLM to emit a tiny `HEADLINE: ... / BOTTOM
   LINE: ...` plain-text format and parse it server-side. The output is so
   small (~6 sentences total) that Pydantic was buying us no real safety
   — a non-empty-string check at parse time covers the same ground.
"""

import re
from datetime import date

from app.agents.cost import estimate_llm_cost
from app.agents.prompts import load_prompt
from app.agents.state import AgentState, Report, SectionOutput
from app.llm import get_synthesis_model

# ─── Output parsing ─────────────────────────────────────────────────
# The prompt instructs the LLM to emit:
#
#     HEADLINE: <one line>
#
#     BOTTOM LINE: <4-6 sentences>
#
# DOTALL on the bottom-line group so it spans newlines (the LLM may break
# the paragraph into multiple lines). IGNORECASE because Nova occasionally
# title-cases the markers ("Headline:" / "Bottom Line:").

_PARSER = re.compile(
    r"HEADLINE:\s*(?P<headline>.+?)\n+BOTTOM\s+LINE:\s*(?P<bottom_line>.+)",
    re.DOTALL | re.IGNORECASE,
)


def _strip_trailing_fence(text: str) -> str:
    """Remove a trailing ``` line if the LLM wrapped its output in a fence.

    The prompt explicitly forbids code fences, but Nova occasionally adds
    them anyway — they're a strong attractor in LLM training data. We
    quietly strip them so the bottom_line doesn't end with "...thesis.\n```".
    """
    cleaned = text.strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[: -len("```")].rstrip()
    return cleaned


def _parse_synthesizer_text(text: str) -> tuple[str, str]:
    """Pull `headline` and `bottom_line` out of the LLM's plain-text response.

    Forgiving on whitespace, case, and stray code fences. Falls back to a
    generic headline if the LLM ignores the format entirely (rare on Nova
    Pro — it follows instructions well — but worth not crashing over).
    """
    # Strip leading ``` (sometimes ``` or ```text) and trailing ``` if present.
    working = text.strip()
    if working.startswith("```"):
        # Drop the opening fence line including any language tag.
        working = working.split("\n", 1)[1] if "\n" in working else ""

    match = _PARSER.search(working)
    if match is not None:
        headline = match.group("headline").strip()
        bottom_line = _strip_trailing_fence(match.group("bottom_line"))
        if headline and bottom_line:
            return headline, bottom_line

    # Fallback: LLM didn't follow format. Use the whole thing as the bottom
    # line and synthesise a placeholder headline so the Report still validates.
    cleaned = _strip_trailing_fence(working) or "(synthesizer returned an empty response)"
    return "Research summary available below.", cleaned


def _normalise_content(content: object) -> str:
    """ChatBedrockConverse returns `content` as either a string OR a list of
    content blocks (Bedrock's multi-block format). Flatten to a single string
    so the regex parser doesn't have to care which one we got.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


# ─── Prompt assembly ────────────────────────────────────────────────


def _format_section_outputs(analyses: list[SectionOutput]) -> str:
    """Render the 5 SectionOutputs into a single prompt-friendly block."""
    if not analyses:
        return "(no section analyses available)"

    blocks: list[str] = []
    for s in analyses:
        # Pretty-print the section name: "financial_health" -> "Financial Health"
        title = s.section.replace("_", " ").title()
        block = f"### {title}\n\n{s.summary}"
        if s.key_points:
            bullets = "\n".join(f"- {p}" for p in s.key_points)
            block += f"\n\nKey points:\n{bullets}"
        blocks.append(block)
    return "\n\n".join(blocks)


# ─── The node ───────────────────────────────────────────────────────


async def synthesizer(state: AgentState) -> dict:
    """Produce the final `Report` for this research run.

    Reads `analyses` (populated by the analyzer fan-out), asks Nova Pro for a
    `HEADLINE: / BOTTOM LINE:` plain-text block, parses it, then assembles
    the Report in Python by joining that prose with the existing SectionOutputs.

    Streaming note: this node uses a plain `.ainvoke` (no
    `with_structured_output`), so when the graph is run via
    `astream(..., stream_mode="messages")`, the tokens this node emits are
    real text tokens — directly renderable in the UI as they arrive.
    """
    analyses = state.get("analyses") or []
    ticker = state["ticker"]
    plan = state.get("plan")

    prompt = load_prompt("synthesizer").format(
        ticker=ticker,
        company_name=(plan.company_name if plan else None) or ticker,
        research_focus=(plan.research_focus if plan else "(no plan)"),
        sections_block=_format_section_outputs(analyses),
    )

    model = get_synthesis_model()
    result = await model.ainvoke(prompt)

    raw_text = _normalise_content(result.content)
    headline, bottom_line = _parse_synthesizer_text(raw_text)

    # Cost accounting:
    # `state["cost_usd"]` at this point holds the sum of planner + 5
    # analyzer LLM calls (operator.add reducer in AgentState merged them).
    # Add the synthesizer's own spend to get the full-run total that we
    # stamp onto the Report. Also return cost_usd so the final AgentState
    # reflects everything — useful for tracing.
    synth_cost = estimate_llm_cost("synthesis", getattr(result, "usage_metadata", None))
    total_cost = float(state.get("cost_usd") or 0.0) + synth_cost

    # Build the section dict, keyed by section name. If somehow two
    # analyses share a section, the later one wins — but the analyzer
    # fan-out only emits one per section, so this is defensive only.
    sections_by_name = {a.section: a for a in analyses}

    report = Report(
        ticker=ticker,
        company_name=plan.company_name if plan else None,
        generated_at=date.today(),
        headline=headline,
        sections=sections_by_name,
        bottom_line=bottom_line,
        cost_usd=total_cost,
    )

    return {"report": report, "cost_usd": synth_cost}
