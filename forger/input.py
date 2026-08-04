"""Classify and normalize skeleton input.

Three input forms are auto-detected:

* FILE -- an existing path to a file containing skeletons.
* CODE -- pasted source code with function signatures (bodies empty/stubbed).
* NL   -- a natural-language description; the agent designs the signature too.

All forms normalize to a :class:`~forger.spec.ModuleSpec`. Signature extraction
(CODE) and signature design (NL) are done by the LLM and returned as JSON.
"""

from __future__ import annotations

import json
import os
import re
from enum import Enum
from pathlib import Path

from forger.spec import FuncSpec, ModuleSpec


class InputKind(str, Enum):
    FILE = "file"
    CODE = "code"
    NL = "nl"


class LanguageRequired(Exception):
    """Raised when NL input needs a target language that has not been set."""


# Patterns that strongly indicate pasted source code rather than prose.
# Each is precise enough that ordinary English (e.g. "a function that returns
# the max of two ints") does not trip it.
_CODE_PATTERNS = [
    re.compile(r"\bdef\b"),                              # python
    re.compile(r"\bfunc\b"),                             # swift / go-ish
    re.compile(r"\bfn\b"),                               # rust
    re.compile(r"\bfunction\s+\w+\s*\("),                # javascript: function name(
    re.compile(r"\b(public|private|protected|static|void|class)\b"),
    re.compile(r"->"),                                   # return-type arrow
    re.compile(r"=>"),                                   # arrow function
    re.compile(r"\([^()]*:\s*[A-Za-z_]\w*[^()]*\)"),     # typed param list, e.g. (a: int)
    re.compile(r";\s*$", re.MULTILINE),                  # statement-terminating semicolon (C, etc.)
    re.compile(r"```"),                                  # already fenced
]


def _looks_like_code(text: str) -> bool:
    return any(pattern.search(text) for pattern in _CODE_PATTERNS)


def classify(text: str) -> InputKind:
    """Decide whether ``text`` is a file path, code, or natural language."""
    stripped = text.strip()
    if not stripped:
        return InputKind.NL

    # A single line that resolves to an existing file.
    if "\n" not in stripped:
        candidate = os.path.expanduser(stripped.strip("'\""))
        if candidate and (os.path.isfile(candidate) or os.path.isfile(os.path.abspath(candidate))):
            return InputKind.FILE

    if _looks_like_code(stripped):
        return InputKind.CODE

    # Signature-shaped text with code punctuation still counts as code.
    if re.search(r"\b\w+\s*\([^)]*\)", stripped) and any(
        c in stripped for c in ("->", "=>", ";", "{", "}")
    ):
        return InputKind.CODE

    return InputKind.NL


def normalize(text: str, kind: InputKind, language: str | None, llm) -> ModuleSpec:
    """Turn raw input of the given kind into a :class:`ModuleSpec`."""
    if kind is InputKind.FILE:
        path = os.path.expanduser(text.strip().strip("'\""))
        code = Path(path).read_text(encoding="utf-8")
        return _normalize_code(code, source="file", language=language, llm=llm)
    if kind is InputKind.CODE:
        return _normalize_code(text, source="code", language=language, llm=llm)
    return _normalize_nl(text, language=language, llm=llm)


_JSON_SHAPE = (
    '{"language": "...", "module_name": "snake_case_slug", '
    '"functions": [{"name": "...", "params": [["a", "int"]], '
    '"return_type": "int", "description": "one line"}]}'
)


def _normalize_code(code: str, source: str, language: str | None, llm) -> ModuleSpec:
    system = (
        "You parse source-code skeletons into structured function signatures. "
        "Detect the programming language. For each function give: name, params "
        "as [name, type] pairs, return_type (or null), and a one-line "
        "description. Respond with ONLY a JSON object of this shape:\n"
        + _JSON_SHAPE
    )
    hint = (
        f"Detected-language hint (may be wrong): {language}."
        if language
        else "No language hint; detect it from the code."
    )
    user = f"{hint}\n\nSkeleton:\n```\n{code}\n```"
    data = _extract_json(llm.complete(system, [{"role": "user", "content": user}]))
    return _module_from_json(data, source=source, fallback=code)


def _normalize_nl(text: str, language: str | None, llm) -> ModuleSpec:
    if not language:
        raise LanguageRequired(
            "Natural-language input needs a target language. "
            "Set one with `:lang <language>` and retry."
        )
    system = (
        "You design function signatures from a natural-language description, "
        "producing one or more functions in the given target language. For each "
        "function give: name, params as [name, type] pairs, return_type (or "
        "null), and a one-line description. Respond with ONLY a JSON object of "
        "this shape:\n" + _JSON_SHAPE
    )
    user = f"Target language: {language}.\n\nDescription:\n{text}"
    data = _extract_json(llm.complete(system, [{"role": "user", "content": user}]))
    return _module_from_json(data, source="nl", fallback=text)


def _module_from_json(data: dict, source: str, fallback: str) -> ModuleSpec:
    language = (data.get("language") or "python").strip().lower()
    functions: list[FuncSpec] = []
    for fd in data.get("functions", []):
        name = fd.get("name")
        if not name:
            continue
        params = [
            (str(p[0]), str(p[1]))
            for p in fd.get("params", [])
            if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
        functions.append(
            FuncSpec(
                name=str(name),
                params=params,
                return_type=fd.get("return_type"),
                description=fd.get("description"),
                target_language=language,
                raw_skeleton=fallback,
            )
        )
    if not functions:
        raise ValueError("Could not extract any function signatures from the input.")
    return ModuleSpec(
        target_language=language,
        functions=functions,
        source=source,
        module_name=data.get("module_name"),
    )


def _extract_json(raw: str) -> dict:
    """Pull the first JSON object out of an LLM response (fenced or bare)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    blob = fenced.group(1) if fenced else raw
    start = blob.find("{")
    end = blob.rfind("}")
    if start != -1 and end != -1 and end > start:
        blob = blob[start : end + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError as e:
        raise ValueError(f"The model did not return valid JSON: {e}\n---\n{raw}") from e
