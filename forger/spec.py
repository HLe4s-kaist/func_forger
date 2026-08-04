"""Pure data types describing definitions and module specs.

A :class:`DefSpec` is a single top-level definition to implement -- not just a
function, but anything a language lets you define at the top level: a function,
a struct/class/trait/enum (``kind="type"``), a type alias/typedef
(``kind="typedef"``), a macro (``kind="macro"``), or a constant/global
(``kind="constant"``). ``kind`` is a language-agnostic bucket chosen by the LLM
during normalization, so no per-language parsing is required.

A :class:`ModuleSpec` is a compilation unit: one or more definitions sharing a
target language. All input forms are normalized into a ``ModuleSpec`` before the
implementer runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DefSpec:
    """A single top-level definition (function, type, macro, ...)."""

    name: str
    kind: str = "function"
    params: list[tuple[str, str]] = field(default_factory=list)  # functions only
    return_type: str | None = None
    signature: str = ""  # canonical signature/shape for any kind
    description: str | None = None
    target_language: str = "python"
    raw_skeleton: str = ""


@dataclass
class ModuleSpec:
    """A compilation unit: one or more definitions in the same target language."""

    target_language: str = "python"
    definitions: list[DefSpec] = field(default_factory=list)
    source: str = "code"  # "code" | "file" | "nl"
    module_name: str | None = None  # suggested file slug; None => derive later
    category: str | None = None  # role bucket, e.g. "arithmetic", "strings", "io"

    def names(self) -> list[str]:
        return [d.name for d in self.definitions]
