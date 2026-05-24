"""LangGraph wiring — compose all nodes into one runnable graph.

Topology (also documented in ARCHITECTURE.md §Phase 2):

    START
      │
      ▼
    planner                              (Haiku — frames the research focus)
      │
      ├──────────────┬──────────────┐
      ▼              ▼              ▼
    fetch_yfinance  fetch_filings  fetch_news     (parallel — 3 sibling edges)
      └──────────────┼──────────────┘
                     ▼
                  indexer                          (chunks → Voyage → Chroma)
                     │
                     │ (conditional edge — Send fan-out, 5 branches)
                     ▼
              analyze_section                      (5 parallel Sonnet calls)
                     │
                     ▼
                 synthesizer                       (Sonnet — final headline + bottom_line)
                     │
                     ▼
                   END

Wiring conventions:

- `add_edge(a, b)` is a plain sequential edge. When `a` has multiple
  outgoing `add_edge` calls (like `planner` → 3 fetchers), LangGraph runs
  the destinations in parallel automatically — no extra config needed.
- `add_conditional_edges(a, fn, [b])` is a conditional edge. `fn` is
  called after `a` completes, and its return value decides routing. We
  use it once, with the Send-API dispatcher from analyzers.py.
- Fan-in is implicit: when 3 fetcher nodes all have edges into `indexer`,
  LangGraph waits for all 3 to complete before running `indexer`. Same
  for the 5 analyzer branches converging into `synthesizer`.

The compiled graph is built once at module import time. `uvicorn --reload`
re-imports the module on code change, so edits to nodes pick up
automatically in dev.
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.nodes.analyzers import analyze_section, fan_out_to_analyzers
from app.agents.nodes.fetchers import fetch_filings, fetch_news, fetch_yfinance
from app.agents.nodes.indexer import indexer
from app.agents.nodes.planner import planner
from app.agents.nodes.synthesizer import synthesizer
from app.agents.state import AgentState


def build_graph() -> CompiledStateGraph:
    """Wire the nodes into a StateGraph and compile it.

    Returns a `CompiledStateGraph` whose `.ainvoke(initial_state)` runs the
    whole pipeline end-to-end and returns the final state dict.
    """
    g: StateGraph = StateGraph(AgentState)

    # ─── Register every node ────────────────────────────────────────
    g.add_node("planner", planner)
    g.add_node("fetch_yfinance", fetch_yfinance)
    g.add_node("fetch_filings", fetch_filings)
    g.add_node("fetch_news", fetch_news)
    g.add_node("indexer", indexer)
    g.add_node("analyze_section", analyze_section)
    g.add_node("synthesizer", synthesizer)

    # ─── Entry ──────────────────────────────────────────────────────
    g.add_edge(START, "planner")

    # ─── Planner fans out to 3 parallel fetchers ────────────────────
    # Three sibling edges from one node = parallel execution in LangGraph.
    g.add_edge("planner", "fetch_yfinance")
    g.add_edge("planner", "fetch_filings")
    g.add_edge("planner", "fetch_news")

    # ─── All 3 fetchers fan in to the indexer ───────────────────────
    # Multiple edges INTO one node = LangGraph waits for all upstream
    # nodes to finish before running this one (super-step semantics).
    g.add_edge("fetch_yfinance", "indexer")
    g.add_edge("fetch_filings", "indexer")
    g.add_edge("fetch_news", "indexer")

    # ─── Indexer fans out to N analyzer branches via Send API ───────
    # `fan_out_to_analyzers` returns a list of Send objects, one per
    # section. LangGraph runs N parallel invocations of analyze_section.
    # The third arg is the list of possible target node names — used
    # for graph visualization and validation.
    g.add_conditional_edges(
        "indexer",
        fan_out_to_analyzers,
        ["analyze_section"],
    )

    # ─── All analyzer branches fan in to the synthesizer ────────────
    # LangGraph waits for ALL Send invocations to complete before
    # routing forward — same super-step pattern as the fetcher fan-in.
    g.add_edge("analyze_section", "synthesizer")

    # ─── Exit ───────────────────────────────────────────────────────
    g.add_edge("synthesizer", END)

    return g.compile()


# Build once at module import. Re-imported (and re-built) when
# uvicorn --reload restarts the process on code change.
research_graph: CompiledStateGraph = build_graph()
