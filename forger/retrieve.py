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
        [candidate.name, candidate.description, candidate.signature, candidate.return_type or ""]
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
