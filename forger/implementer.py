"""Turn a module spec into implemented source code via the LLM, reusing the
existing library wherever possible.

One prompt covers the whole module (all of its functions) and expects a single
fenced code block back -- the complete module source. The body is then scanned
for references to known library function names so dependencies can be recorded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from forger.manifest import ManifestEntry
from forger.spec import ModuleSpec

SYSTEM_PROMPT = """\
You implement function bodies for a target programming language, reusing an
existing function library.

Hard rules:
- Emit ONLY the requested functions, with the exact signatures given.
- Where reasonable, CALL existing library functions instead of reimplementing \
their logic. Import them correctly for the target language.
- Never invent or call a library function that was not listed as available.
- Include any imports the module needs.
- Output a single fenced code block containing the complete module source.
"""


@dataclass
class ImplementedModule:
    """The implemented module source plus the library functions it references."""

    code: str
    used_names: list[str] = field(default_factory=list)


def build_user_prompt(module: ModuleSpec, retrieved: list[ManifestEntry]) -> str:
    lines = [f"Target language: {module.target_language}", "", "Definitions to implement:"]
    for fn in module.definitions:
        if fn.kind == "function":
            params = ", ".join(f"{n}: {t}" for n, t in fn.params)
            ret = f" -> {fn.return_type}" if fn.return_type else ""
            lines.append(f"- [function] {fn.name}({params}){ret}")
        else:
            head = f"- [{fn.kind}] {fn.name}"
            if fn.signature:
                head += f"  ::  {fn.signature}"
            lines.append(head)
        if fn.description:
            lines.append(f"    {fn.description}")

    lines.append("")
    if retrieved:
        lines.append("Available library definitions (prefer reusing these):")
        for entry in retrieved:
            lines.append(f"- [{entry.kind}] {entry.signature}  -- {entry.description}")
            lines.append(f"    defined in: {entry.file_path}")
    else:
        lines.append("No library definitions are available yet for this language.")

    return "\n".join(lines)


def implement(module: ModuleSpec, retrieved: list[ManifestEntry], llm) -> str:
    """Call the LLM and return its raw response (a code block, hopefully)."""
    user = build_user_prompt(module, retrieved)
    return llm.complete(SYSTEM_PROMPT, [{"role": "user", "content": user}])


def parse(raw: str, module: ModuleSpec, known_names: set[str]) -> ImplementedModule:
    """Extract the code block and the library function names it references."""
    code = extract_code_block(raw)
    if not code and _looks_like_raw_code(raw):
        code = raw.strip()
    if not code:
        raise ValueError("The model returned no code block to parse.")

    own_names = {fn.name for fn in module.definitions}
    used = sorted(
        name
        for name in known_names
        if name not in own_names and re.search(rf"\b{re.escape(name)}\b", code)
    )
    return ImplementedModule(code=code, used_names=used)


def extract_code_block(raw: str) -> str:
    match = re.search(r"```(?:[a-zA-Z0-9_+.\-#]*)?\n(.*?)```", raw, re.DOTALL)
    return match.group(1).strip() if match else ""


def _looks_like_raw_code(raw: str) -> bool:
    """Heuristic: a model returned code without bothering to fence it."""
    return bool(re.search(r"\b(def|func|fn|function|class)\b", raw)) or raw.count("{") >= 1
