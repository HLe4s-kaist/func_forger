"""Agentic forging.

The implementer is an *agent* with one tool: library search. It runs a small
ReAct-style loop using a plain-text tool protocol so it works with any LLM
backend (Anthropic, OpenAI-compatible, GLM proxies, ...) -- no provider
tool-use API required:

1. The model may emit ``SEARCH: <query>`` lines to find reusable library
   functions before writing code.
2. We execute each search (:func:`forger.retrieve.search_library`) and feed the
   results back.
3. The loop ends when the model emits the implemented module as a fenced code
   block.

SEARCH/REUSE IS THE HEART OF THE PROJECT, so the model is required to write
rustdoc-grade documentation for every function. That documentation becomes the
search index that lets future functions find and reuse this one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from forger.embeddings import Embedder
from forger.implementer import extract_code_block
from forger.manifest import Manifest, ManifestEntry
from forger.retrieve import search_library

# When the same-language library is small, seed the agent with EVERY function so
# it always sees each one's exact calling convention (no search miss -> no
# guessed/wrong signatures). Only rank by relevance once the library is large.
SEED_ALL_LIMIT = 20

AGENT_SYSTEM = """\
You implement top-level definitions in a target programming language, reusing \
an existing library. A "definition" is anything the language declares at the \
top level -- not only functions, but structs/classes/traits/enums, type \
aliases/typedefs, macros, and constants/globals. A skeleton may declare \
several of these together; implement ALL of them.

THE MOST IMPORTANT PART OF YOUR OUTPUT IS THE DOCUMENTATION. Definitions are \
later discovered by searching their documentation, so for EVERY definition \
write detailed, rustdoc-style doc comments immediately above it. Use the \
comment syntax of the target language (// or /// for C/C++/Rust/Go/JS/Java; # \
or a triple-quoted docstring for Python). Each doc block must contain:
- A one-line summary of what the definition is/does.
- For functions: "Arguments:" (each name, type, meaning) and "Returns:".
- For types/structs/macros: the fields/form/parameters and their meaning.
- "Behavior:" -- how it works, step by step, and which existing library \
definitions it uses and why.
Write specific, searchable prose -- use the words someone would type to find \
this definition again. Add concise inline comments on non-obvious lines.

BEFORE writing any code, you MUST check the library for definitions you can \
reuse (functions to call, types/macros to reference). This is mandatory. You \
have two tools, each written as its own line:
- `LOOKUP: <name>` -- get the EXACT kind, signature, params, return type, and \
doc of a specific definition. Use this whenever the user references a \
definition by name, and before you use ANY definition whose exact form is not \
already in front of you.
- `SEARCH: <query>` -- find definitions by purpose (free text), e.g.
    SEARCH: sum two integers
You will receive the results. Run as many LOOKUP/SEARCH lines as you need, THEN \
write the code. NEVER use a library definition whose exact form you have not \
seen in a LOOKUP/SEARCH result or the available list -- if unsure, LOOKUP it \
first.

When ready, output the COMPLETE implemented module in ONE fenced code block.
- Implement EVERY top-level definition in the skeleton, keeping their \
signatures/shapes.
- REUSE IS STRONGLY PREFERRED. If ANY listed/looked-up definition can help -- a \
function to call, or a type/struct/macro to reference -- use it (importing or \
declaring it correctly for the target language) instead of reimplementing or \
redefining. NEVER invent or use a library definition that did not appear in a \
LOOKUP/SEARCH result or the available list.
- When you use a library function, copy its signature EXACTLY -- the same \
argument ORDER, TYPES, and COUNT, taken from a LOOKUP/SEARCH result or the \
available list. Never guess.
- Include any imports the module needs.
- After any SEARCH: lines, output ONLY the code block.
"""


class ForgeError(Exception):
    """Raised when the agent fails to produce implemented code."""


@dataclass
class ForgeResult:
    """The output of a successful forge."""

    code: str
    used_names: list[str] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)
    raw_turns: list[str] = field(default_factory=list)

    @property
    def unknown_deps(self) -> list[str]:
        """Library calls in the code that are not actually in the library."""
        return getattr(self, "_unknown_deps", [])


_SEARCH_RE = re.compile(r"^\s*SEARCH:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)


def _parse_searches(raw: str) -> list[str]:
    return [m.group(1) for m in _SEARCH_RE.finditer(raw)]


def _format_results(query: str, hits: list[ManifestEntry]) -> str:
    if not hits:
        return f"SEARCH {query!r}: (no matches)"
    lines = [f"SEARCH {query!r}:"]
    for entry in hits:
        lines.append(f"  - [{entry.kind}] {entry.signature}  -- {entry.description}")
        lines.append(f"    defined in: {entry.file_path}")
        if entry.doc:
            lines.append("    doc: " + entry.doc.replace("\n", "\n    "))
    return "\n".join(lines)


_LOOKUP_RE = re.compile(r"^\s*LOOKUP:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)


def _parse_lookups(raw: str) -> list[str]:
    return [m.group(1).strip() for m in _LOOKUP_RE.finditer(raw)]


def _format_lookup(name: str, manifest: Manifest, language: str) -> str:
    """Exact info for a named function -- the authoritative calling convention."""
    target = name.strip()
    entry = manifest.get(language, target)
    if entry is None:
        return f"LOOKUP {target!r}: (not found in the library)"
    lines = [f"LOOKUP {target!r}:", f"  kind: {entry.kind}", f"  signature: {entry.signature}"]
    if entry.params:
        params = ", ".join(f"{p.get('name', '')}: {p.get('type', '')}" for p in entry.params)
        lines.append(f"  params: {params}")
    if entry.return_type:
        lines.append(f"  returns: {entry.return_type}")
    lines.append(f"  defined in: {entry.file_path}")
    if entry.doc:
        lines.append("  doc: " + entry.doc.replace("\n", "\n  "))
    return "\n".join(lines)


def _used_names(
    code: str, manifest: Manifest, language: str, own_names: set[str] | None = None
) -> tuple[list[str], list[str]]:
    """Return (known_used, unknown_used) library names referenced in ``code``.

    ``own_names`` are the names defined in this very module; they are excluded
    from "unknown calls" so a function's own definition header (and sibling
    definitions) are not flagged.
    """
    own_names = own_names or set()
    library_names = {e.name for e in manifest.all() if e.target_language == language}
    known = sorted(
        name for name in library_names if re.search(rf"\b{re.escape(name)}\b", code)
    )
    called = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", code))
    builtins_ignore = {
        # control flow / python builtins
        "if", "for", "while", "switch", "case", "return", "print", "def", "len",
        "range", "str", "int", "float", "list", "dict", "set", "sum", "min",
        "max", "abs", "any", "all", "sorted", "map", "filter", "open", "type",
        "isinstance",
        # common C stdlib (so they aren't flagged as unknown library calls)
        "printf", "sprintf", "snprintf", "scanf", "puts", "putchar", "getchar",
        "malloc", "calloc", "realloc", "free", "fopen", "fclose", "fread",
        "fwrite", "fseek", "ftell", "memcpy", "memset", "strcpy", "strcmp",
        "strlen", "assert", "exit", "atoi", "atof",
    }
    unknown: list[str] = []
    for name in sorted(called):
        if name in library_names or name in own_names or name in builtins_ignore:
            continue
        unknown.append(name)
    return known, unknown


def forge_documented(
    skeleton: str,
    language: str,
    manifest: Manifest,
    llm,
    max_turns: int = 6,
    on_turn=None,
    own_names: set[str] | None = None,
    embedder: Embedder | None = None,
    on_chunk=None,
) -> ForgeResult:
    """Run the agent to turn ``skeleton`` into a documented, implemented module.

    ``on_turn(raw, searches, code)`` is called after each model response so the
    UI can show progress (e.g. "searching: ..."). It is optional.
    """
    # Auto-seed: surface reusable library functions up front so the agent composes
    # them into the new code even when it does not emit a SEARCH query. For a small
    # library, seed ALL same-language functions so the agent knows every exact
    # signature (prevents wrong argument count/type/count at the call site).
    same_language = [e for e in manifest.all() if e.target_language == language]
    if len(same_language) <= SEED_ALL_LIMIT:
        seed_hits = same_language
    else:
        seed_hits = search_library(skeleton, manifest, language, top_k=12, embedder=embedder)
    context = ""
    if seed_hits:
        lines = ["Available library definitions (REUSE these wherever reasonable):"]
        for entry in seed_hits:
            lines.append(f"- [{entry.kind}] {entry.signature}  ({entry.file_path})")
            if entry.doc:
                lines.append("    " + entry.doc.replace("\n", "\n    "))
        context = "\n".join(lines) + "\n\n"

    messages = [
        {
            "role": "user",
            "content": (
                f"Target language: {language}\n\n{context}"
                f"Skeleton (the bones plus optional direction comments). "
                f"Implement it:\n```\n{skeleton}\n```"
            ),
        }
    ]
    all_searches: list[str] = []
    raw_turns: list[str] = []

    for _ in range(max_turns):
        raw = llm.complete(AGENT_SYSTEM, messages, on_chunk=on_chunk)
        raw_turns.append(raw)
        new_searches = _parse_searches(raw)
        new_lookups = _parse_lookups(raw)
        code = extract_code_block(raw)
        if on_turn:
            on_turn(raw, new_searches + new_lookups, code)

        if code:
            known, unknown = _used_names(code, manifest, language, own_names)
            result = ForgeResult(
                code=code,
                used_names=known,
                searches=all_searches,
                raw_turns=raw_turns,
            )
            result._unknown_deps = unknown  # type: ignore[attr-defined]
            return result

        if new_searches or new_lookups:
            all_searches.extend(new_searches)
            all_searches.extend(f"LOOKUP:{n}" for n in new_lookups)
            blocks = [
                _format_results(q, search_library(q, manifest, language, embedder=embedder))
                for q in new_searches
            ]
            blocks.extend(
                _format_lookup(name, manifest, language) for name in new_lookups
            )
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": "Library lookup results:\n\n"
                    + "\n\n".join(blocks)
                    + "\n\nNow write the implemented module in a single fenced code block.",
                }
            )
        else:
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": "Output the implemented module now in a single fenced code block.",
                }
            )

    raise ForgeError("The agent did not produce code within the turn budget.")


REGEN_SYSTEM = """\
You revise part of a code module. Modify ONLY the indicated region; leave every \
other line byte-for-byte unchanged. Keep the same language and keep the \
function's rustdoc-style documentation up to date. Output the ENTIRE module in \
a single fenced code block.
"""


def regen_range(
    full_code: str, region: str, instruction: str, language: str, llm
) -> str:
    """Regenerate only ``region`` of ``full_code`` per ``instruction``.

    The constraint "modify only this region, leave everything else unchanged" is
    baked into the prompt so the developer only has to select a range and,
    optionally, describe the change. Returns the full module with only the
    selected region changed.
    """
    user = (
        f"Target language: {language}\n\n"
        f"Full module:\n```{full_code}```\n\n"
        f"Region to revise (modify ONLY this):\n```\n{region}\n```\n\n"
        f"Instruction: {instruction or 'improve this region'}."
    )
    raw = llm.complete(REGEN_SYSTEM, [{"role": "user", "content": user}])
    code = extract_code_block(raw)
    if not code:
        raise ForgeError("Range regeneration produced no code.")
    return code
