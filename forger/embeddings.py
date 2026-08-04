"""Optional semantic (embedding) search over the library.

The default search is dependency-free token overlap. This module adds an
*optional* embedding backend: when configured, the library's definitions are
embedded with a local model (downloaded on first use) and ranked by cosine
similarity to the query. This catches semantic matches that token overlap
misses (e.g. "sum two integers" <-> "add returns the arithmetic sum").

Design notes:
- Zero hard dependencies. ``NullEmbedder`` (always available) is the default;
  embedding search only activates when a backend (fastembed or
  sentence-transformers) is installed AND selected via config. If construction
  fails for any reason we silently fall back to NullEmbedder, so the app always
  works.
- Entry-text vectors are cached by text hash, so re-embedding only happens when
  a definition's documentation changes.
- No numpy in the public interface; vectors are ``list[float]`` and cosine is
  computed in pure Python (libraries here are small).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Protocol

# Default local models (downloaded & cached on first use).
DEFAULT_MODELS = {
    "fastembed": "BAAI/bge-small-en-v1.5",
    "sentence-transformers": "sentence-transformers/all-MiniLM-L6-v2",
}


class Embedder(Protocol):
    """A text embedder with an internal vector cache."""

    def available(self) -> bool: ...

    def embed_query(self, query: str) -> list[float]: ...

    def embed_one(self, text: str) -> list[float]: ...


class NullEmbedder:
    """No-op embedder: embedding search is disabled (token search is used)."""

    def available(self) -> bool:
        return False

    def embed_query(self, query: str) -> list[float]:
        raise RuntimeError("No embedder configured.")

    def embed_one(self, text: str) -> list[float]:
        raise RuntimeError("No embedder configured.")


class _FastEmbedBackend:
    """Backend using `fastembed` (ONNX-based, no torch)."""

    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding  # local import: optional dependency

        self._model = TextEmbedding(model_name=model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, vec)) for vec in self._model.embed(texts)]


class _SentenceTransformersBackend:
    """Backend using `sentence-transformers` (torch-based)."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # optional dependency

        self._model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, vec)) for vec in self._model.encode(texts)]


class CachedEmbedder:
    """Wraps a backend and caches vectors by text hash."""

    def __init__(self, backend) -> None:
        self._backend = backend
        self._cache: dict[int, list[float]] = {}

    def available(self) -> bool:
        return True

    def _embed_cached(self, text: str) -> list[float]:
        key = hash(text)
        vector = self._cache.get(key)
        if vector is None:
            vector = self._backend.embed([text])[0]
            self._cache[key] = vector
        return vector

    def embed_query(self, query: str) -> list[float]:
        return self._embed_cached(query)

    def embed_one(self, text: str) -> list[float]:
        return self._embed_cached(text)


def make_embedder(config) -> Embedder:
    """Build the configured embedder, gracefully falling back to NullEmbedder."""
    provider = (getattr(config, "embed_provider", None) or "none").strip().lower()
    if provider in ("", "none", "off", "disabled"):
        return NullEmbedder()
    model = getattr(config, "embed_model", None) or DEFAULT_MODELS.get(provider)
    try:
        if model and Path(model).is_dir():
            # A local model directory (e.g. a bundled/restored model) loads
            # directly via sentence-transformers, regardless of the provider.
            backend = _SentenceTransformersBackend(model)
        elif provider == "fastembed":
            backend = _FastEmbedBackend(model)
        elif provider in ("sentence-transformers", "sbert", "sentence_transformers"):
            backend = _SentenceTransformersBackend(model)
        else:
            return NullEmbedder()
        return CachedEmbedder(backend)
    except Exception:
        # Missing dependency, download failure, etc.: degrade to token search.
        return NullEmbedder()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na ** 0.5 * nb ** 0.5)


def _entry_text(entry) -> str:
    return f"{entry.name}\n{entry.description}\n{entry.doc}"


def embedding_search(query: str, entries: Iterable, embedder: Embedder, top_k: int = 8) -> list:
    """Rank ``entries`` by cosine similarity of their text to ``query``.

    A small exact-name bonus is added so a query naming a function still wins.
    """
    query_vec = embedder.embed_query(query)
    query_lower = query.lower()
    scored = []
    for entry in entries:
        score = _cosine(query_vec, embedder.embed_one(_entry_text(entry)))
        if entry.name.lower() in query_lower or query_lower in entry.name.lower():
            score += 0.3
        if score > 0.1:
            scored.append((entry, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [entry for entry, _ in scored[:top_k]]
