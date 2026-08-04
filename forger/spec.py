"""Pure data types describing function and module specs.

A :class:`ModuleSpec` is a compilation unit: one or more functions sharing a
target language. All three input forms (pasted code, file path, natural
language) are normalized into a ``ModuleSpec`` before the implementer runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FuncSpec:
    """A single function to implement.

    ``params`` is a list of ``(name, type)`` tuples; the type may be a
    placeholder such as ``"any"`` / ``"unknown"`` when not specified.
    """

    name: str
    params: list[tuple[str, str]] = field(default_factory=list)
    return_type: str | None = None
    description: str | None = None
    target_language: str = "python"
    raw_skeleton: str = ""


@dataclass
class ModuleSpec:
    """A compilation unit: one or more functions in the same target language."""

    target_language: str = "python"
    functions: list[FuncSpec] = field(default_factory=list)
    source: str = "code"  # "code" | "file" | "nl"
    module_name: str | None = None  # suggested file slug; None => derive later
    category: str | None = None  # role bucket, e.g. "arithmetic", "strings", "io"

    def names(self) -> list[str]:
        return [f.name for f in self.functions]
