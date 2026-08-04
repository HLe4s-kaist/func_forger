"""Retrieve reusable library functions.

Search/reuse is the heart of Func-Forger, so retrieval is tuned for precision:

* Identifiers are split into words (``double_sum`` and ``doubleValue`` both
  index as ``{double, sum}`` / ``{double, value}``), and generic structural
  stopwords (``function``, ``return``, ``argument``, ``value``, ...) are
  dropped so content words dominate.
* Scoring weights the function **name** highest, then its short **description**,
  then its full **doc** comment, plus a strong name-substring bonus (and a
  type-shape bonus in :func:`retrieve`). The result is that a query like
  ``"sum two integers"`` surfaces ``add`` even when the query and the name share
  no surface form.

:func:`search_library` backs the agent's free-text search tool and the
auto-seed of candidates; :func:`retrieve` backs signature-based matching. Both
return ``list[ManifestEntry]`` so an embeddings backend can drop in later.
"""

from __future__ import annotations

import re

from forger.manifest import Manifest, ManifestEntry
from forger.spec import FuncSpec

# A "word" is a letter run optionally continued by digits; separators and
# camelCase boundaries are split afterwards so identifiers index by words.
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Generic structural words that add noise to code search. Deliberately kept
# small so meaningful domain words are never lost.
_STOPWORDS = {
    "a", "an", "the", "of", "to", "and", "or", "for", "with", "in", "on", "at",
    "by", "as", "is", "are", "be", "this", "that", "it", "its", "from", "into",
    "using", "use", "used", "via", "which", "if", "then", "else", "function",
    "functions", "func", "fn", "def", "method", "return", "returns", "returned",
    "argument", "arguments", "arg", "args", "parameter", "parameters", "param",
    "params", "value", "values", "variable", "variables", "given", "new", "make",
    "get", "set", "put", "let", "const", "var", "result", "results", "output",
    "outputs", "input", "inputs", "example", "description", "one", "two",
    "three", "four", "five", "first", "second", "third",
}


def _tokens(text: str | None) -> set[str]:
    """Split text into lowercase content words, dropping stopwords."""
    if not text:
        return set()
    out: set[str] = set()
    for word in _WORD_RE.findall(text):
        for sub in _CAMEL_BOUNDARY.split(word):
            token = sub.lower()
            if len(token) > 1 and token not in _STOPWORDS:
                out.add(token)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _section_score(query: set[str], name: str, description: str, doc: str) -> float:
    """Weighted overlap: name (3x) > description (1.5x) > doc (1x)."""
    if not query:
        return 0.0
    name_t = _tokens(name)
    desc_t = _tokens(description)
    doc_t = _tokens(doc)
    score = 0.0
    if name_t:
        score += 3.0 * _jaccard(query, name_t)
    if desc_t:
        score += 1.5 * _jaccard(query, desc_t)
    if doc_t:
        score += 1.0 * _jaccard(query, doc_t)
    return score


def search_library(
    query: str, manifest: Manifest, language: str | None = None, top_k: int = 8
) -> list[ManifestEntry]:
    """Free-text search over the library (the agent's search tool + auto-seed)."""
    query_tokens = _tokens(query)
    query_lower = (query or "").lower()
    if not query_tokens and not query_lower:
        return []

    scored: list[tuple[ManifestEntry, float]] = []
    for entry in manifest.all():
        if language and entry.target_language != language:
            continue
        score = _section_score(query_tokens, entry.name, entry.description, entry.doc)
        if query_lower and (
            entry.name.lower() in query_lower or query_lower in entry.name.lower()
        ):
            score += 2.0
        if score > 0:
            scored.append((entry, score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [entry for entry, _ in scored[:top_k]]


def retrieve(spec: FuncSpec, manifest: Manifest, top_k: int = 8) -> list[ManifestEntry]:
    """Return up to ``top_k`` same-language library functions relevant to ``spec``."""
    query_tokens = _tokens(" ".join([spec.name, spec.description or ""]))
    query_tokens |= _tokens(" ".join(f"{n} {t}" for n, t in spec.params))
    if spec.return_type:
        query_tokens |= _tokens(spec.return_type)
    name_lower = (spec.name or "").lower()

    spec_types = {t.lower() for _, t in spec.params} | (
        {spec.return_type.lower()} if spec.return_type else set()
    )

    scored: list[tuple[ManifestEntry, float]] = []
    for candidate in manifest.all():
        if candidate.target_language != spec.target_language or candidate.name == spec.name:
            continue
        score = _section_score(
            query_tokens, candidate.name, candidate.description, candidate.doc
        )
        if name_lower and (
            candidate.name.lower() in name_lower or name_lower in candidate.name.lower()
        ):
            score += 2.0
        # Type-shape bonus surfaces helpers that share a signature shape.
        cand_types = {p.get("type", "").lower() for p in candidate.params} | (
            {candidate.return_type.lower()} if candidate.return_type else set()
        )
        if spec_types and cand_types:
            score += 0.8 * (len(spec_types & cand_types) / len(spec_types | cand_types))
        if score > 0:
            scored.append((candidate, score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [candidate for candidate, _ in scored[:top_k]]
