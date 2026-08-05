"""Data types and code-block extraction for the implementer pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ImplementedModule:
    """The implemented module source plus the library functions it references."""

    code: str
    used_names: list[str] = field(default_factory=list)


def extract_code_block(raw: str) -> str:
    """Extract the first fenced code block from an LLM response.

    Uses a greedy match (``(.*)``) so inner ```` ``` ```` fences inside
    docstrings/examples don't truncate the output.
    """
    match = re.search(r"```(?:[a-zA-Z0-9_+.\-#]*)?[ \t]*\n(.*)```", raw, re.DOTALL)
    return match.group(1).strip() if match else ""
