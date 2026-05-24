"""Prompt loader.

Prompts live in `backend/app/prompts/` as `.md` files. Each node imports
`load_prompt("name")` and calls `.format(...)` to fill in placeholders.

Why files and not inline strings:
- Easier to edit (markdown editor, syntax highlighting).
- Easier to diff in code review (real line-level changes, not escaped strings).
- The /prompts directory becomes a self-contained "prompt library" that's
  searchable and grep-able when you're hunting for prompt drift.

`@cache` makes the first read pay the disk hit, subsequent reads are
in-memory. Restart the server (`uvicorn --reload` already does this on
code change) to pick up edits to the .md file itself.
"""

from functools import cache
from pathlib import Path

# Resolve once at import time: /backend/app/prompts/
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@cache
def load_prompt(name: str) -> str:
    """Read a prompt template by name (without the .md extension).

    Examples:
        load_prompt("planner")               -> app/prompts/planner.md
        load_prompt("analyzers/risks")       -> app/prompts/analyzers/risks.md
    """
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(
            f"Prompt template not found: {path}. "
            f"Expected a .md file under backend/app/prompts/."
        )
    return path.read_text(encoding="utf-8")
