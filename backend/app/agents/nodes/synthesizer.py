"""Synthesizer node — assembles the final Report.

This is the fan-in counterpart to the analyzer fan-out: 5 SectionOutputs go
in, 1 Report comes out.

Design choice: the synthesizer does NOT regenerate the section content.
Each SectionOutput already carries the analyst's summary, key points, and
citations — replaying those through Sonnet would (a) burn tokens and
(b) invite drift from what the analyst actually said. Instead, the
synthesizer's job is the report-level prose only: a one-line `headline`
and a 4–6 sentence `bottom_line` (bull case / bear case / neutral verdict).
We then construct the final `Report` in Python by combining that prose
with the existing analyses.
"""

from datetime import date

from pydantic import BaseModel, Field

from app.agents.prompts import load_prompt
from app.agents.state import AgentState, Report, SectionOutput
from app.llm import get_synthesis_model


class SynthesizerOutput(BaseModel):
    """The narrow structured-output schema for the synthesizer's LLM call.

    Module-local on purpose (not in state.py) because it's an internal
    implementation detail — the public `Report` model is what callers see.

    Note: no leading underscore on the class name. langchain strips leading
    underscores when generating the LLM-facing tool name, and then can't
    match the model's response back to the registered class. Public class
    names (no `_`) are required for `with_structured_output(...)` to work.
    """

    headline: str = Field(
        ...,
        description="One-line investment summary, ≤140 chars, plain prose.",
    )
    bottom_line: str = Field(
        ...,
        description=(
            "4–6 sentences covering bull case, bear case, and a neutral verdict. "
            "Qualitative only — specific numbers live in the section analyses."
        ),
    )


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


async def synthesizer(state: AgentState) -> dict:
    """Produce the final `Report` for this research run.

    Reads `analyses` (populated by the analyzer fan-out), asks Sonnet for a
    headline + bottom_line, then assembles the Report in Python by joining
    that prose with the existing SectionOutputs.
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

    model = get_synthesis_model().with_structured_output(SynthesizerOutput)
    out: SynthesizerOutput = await model.ainvoke(prompt)

    # Build the section dict, keyed by section name. If somehow two
    # analyses share a section, the later one wins — but the analyzer
    # fan-out only emits one per section, so this is defensive only.
    sections_by_name = {a.section: a for a in analyses}

    report = Report(
        ticker=ticker,
        company_name=plan.company_name if plan else None,
        generated_at=date.today(),
        headline=out.headline,
        sections=sections_by_name,
        bottom_line=out.bottom_line,
    )

    return {"report": report}
