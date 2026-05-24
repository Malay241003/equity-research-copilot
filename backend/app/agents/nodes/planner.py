"""Planner node — the first stop in the graph.

Reads ticker + optional query from state, asks the fast model (Haiku) to
frame the research angle, returns a typed `Plan`.

Why Haiku not Sonnet: planning is a cheap structured task — short prompt,
short output, no synthesis. Spending Sonnet money here would be wasteful.

Why `with_structured_output(Plan)`: we want a `Plan` object back, not free
text. LangChain handles the schema-to-tool-call dance under the hood; we
get a validated Pydantic model out the other side. If the LLM produces
invalid JSON, Pydantic raises a `ValidationError` here — caught upstream
by the graph runner.
"""

from app.agents.prompts import load_prompt
from app.agents.state import ALL_SECTIONS, AgentState, Plan
from app.llm import get_fast_model


async def planner(state: AgentState) -> dict:
    """Produce a research `Plan` for the given ticker.

    Returns a partial state update of the form `{"plan": Plan(...)}`.
    LangGraph merges this back into AgentState.
    """
    ticker = state["ticker"]
    query = state.get("query") or "(none)"

    prompt_text = load_prompt("planner").format(ticker=ticker, query=query)

    model = get_fast_model().with_structured_output(Plan)
    plan: Plan = await model.ainvoke(prompt_text)

    # Trust-but-verify: the LLM produced the Plan, but we own a few invariants.
    # - `ticker` must match the input exactly (defense against typos / spelling fixes).
    # - `sections_to_run` is locked to all 5 in v1 per PHASE_NOTES.md decision —
    #   we re-pin it here so the LLM cannot accidentally drop a section.
    plan = plan.model_copy(
        update={
            "ticker": ticker.upper(),
            "sections_to_run": list(ALL_SECTIONS),
        }
    )

    return {"plan": plan}
