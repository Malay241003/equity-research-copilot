"""One Python module per LangGraph node.

Each module exports a callable `async def <node_name>(state: AgentState) -> dict`
returning a partial state update. Nodes must never mutate state directly —
they return the diff and LangGraph merges it.
"""
