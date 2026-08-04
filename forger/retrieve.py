"""Retrieve reusable library functions for a function being implemented.

Only same-language functions are candidates: a Python function cannot call a C
function without an FFI story, which is out of scope. Scoring is a cheap
token-overlap heuristic so it is fast and dependency-free; an embeddings backend
can drop in later behind the same :func:`retrieve` signature without touching
callers.
"""

from __future__ import annotations

import re

from forger.manifest import Manifest, ManifestEntry
from forger.spec import FuncSpec

_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    return {t.lower() for t in _TOKEN.findall(text)}


def _score(candidate: ManifestEntry, spec: FuncSpec) -> float:
    spec_blob = " ".join(
        [spec.name, spec.description or "", spec.return_type or ""]
        + [f"{n} {t}" for n, t in spec.params]
    )
    cand_blob = " ".join(
        [candidate.name, candidate.description, candidate.doc, candidate.signature, candidate.return_type or ""]
        + [f"{p.get('name', '')} {p.get('type', '')}" for p in candidate.params]
    )
    spec_tokens = _tokens(spec_blob)
    cand_tokens = _tokens(cand_blob)
    if not spec_tokens or not cand_tokens:
        return 0.0

    # Name similarity (substring either way) is a strong signal.
    name_hit = 2.0 if (
        candidate.name.lower() in spec.name.lower()
        or spec.name.lower() in candidate.name.lower()
    ) else 0.0

    # Token overlap across the whole blob (names, types, description words).
    overlap = len(spec_tokens & cand_tokens) / len(spec_tokens | cand_tokens)

    # Type overlap (argument + return types) surfaces helpers that share a shape.
    spec_types = {t.lower() for _, t in spec.params} | (
        {spec.return_type.lower()} if spec.return_type else set()
    )
    cand_types = {p.get("type", "").lower() for p in candidate.params} | (
        {candidate.return_type.lower()} if candidate.return_type else set()
    )
    type_overlap = len(spec_types & cand_types) / max(1, len(spec_types | cand_types))

    return name_hit + overlap + type_overlap


def search_library(
    query: str, manifest: Manifest, language: str | None = None, top_k: int = 8
) -> list[ManifestEntry]:
    """Free-text search over the library, used by the agent's search tool.

    Scores same-language entries by token overlap with the query (plus a strong
    name-substring bonus). When ``language`` is given, results are restricted to
    that language so the agent only sees functions it can actually call.
    """
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    query_lower = query.lower()

    scored: list[tuple[ManifestEntry, float]] = []
    for entry in manifest.all():
        if language and entry.target_language != language:
            continue
        entry_tokens = _tokens(
            " ".join(
                [entry.name, entry.description, entry.doc, entry.signature, entry.return_type or ""]
            )
        )
        if not entry_tokens:
            continue
        score = len(query_tokens & entry_tokens) / len(query_tokens | entry_tokens)
        if entry.name.lower() in query_lower or query_lower in entry.name.lower():
            score += 1.0
        if score > 0:
            scored.append((entry, score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [entry for entry, _ in scored[:top_k]]


def retrieve(spec: FuncSpec, manifest: Manifest, top_k: int = 8) -> list[ManifestEntry]:
    """Return up to ``top_k`` same-language library functions relevant to ``spec``."""
    candidates = [
        entry
        for entry in manifest.all()
        if entry.target_language == spec.target_language and entry.name != spec.name
    ]
    scored = [(entry, _score(entry, spec)) for entry in candidates]
    scored = [(entry, score) for entry, score in scored if score > 0]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [entry for entry, _ in scored[:top_k]]
