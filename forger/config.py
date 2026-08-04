"""Configuration: LLM provider/model/credentials, library location, and the
session's current target language.

Resolution precedence for every field: explicit REPL value > environment
variable > built-in default. Provider credentials are read from the usual
environment variables so the tool works with any open-source or proprietary
backend without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL_BY_PROVIDER = {
    "anthropic": "claude-sonnet-4-6",
    "openai-compat": "gpt-4o-mini",
}

LANGUAGE_ALIASES = {
    "py": "python",
    "python3": "python",
    "ts": "typescript",
    "tsx": "typescript",
    "js": "javascript",
    "jsx": "javascript",
    "node": "javascript",
    "rs": "rust",
    "cpp": "cpp",
    "c++": "cpp",
    "cc": "cpp",
    "golang": "go",
}


def canonical_language(lang: str | None) -> str | None:
    """Normalize a language alias (``py`` -> ``python``) or return ``None``."""
    if not lang:
        return None
    lang = lang.strip().lower()
    return LANGUAGE_ALIASES.get(lang, lang) or None


@dataclass
class Config:
    """Mutable session configuration.

    Fields are plain attributes so the REPL can mutate them with ``:lang`` /
    ``:model`` / ``:provider`` / ``:lib`` without rebuilding the object.
    """

    library_dir: Path = Path("./library")
    provider: str = "anthropic"
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    session_language: str | None = None  # canonical, or None
    embed_provider: str | None = None  # none | fastembed | sentence-transformers
    embed_model: str | None = None  # local embedding model name
    manifest_override: Path | None = None  # keep the index outside library_dir

    def resolved_model(self) -> str:
        if self.model:
            return self.model
        env = os.environ.get("FORGER_MODEL")
        if env:
            return env
        return DEFAULT_MODEL_BY_PROVIDER.get(self.provider, "gpt-4o-mini")

    def resolved_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        if self.provider == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
                "ANTHROPIC_AUTH_TOKEN"
            )
        return os.environ.get("OPENAI_API_KEY")

    def resolved_base_url(self) -> str | None:
        if self.base_url:
            return self.base_url
        if self.provider == "anthropic":
            return os.environ.get("ANTHROPIC_BASE_URL")
        return os.environ.get("OPENAI_BASE_URL")

    @property
    def manifest_path(self) -> Path:
        # When overridden, the manifest (index) lives outside library_dir -- used
        # so ingesting an existing repo never writes anything into that repo.
        if self.manifest_override is not None:
            return self.manifest_override
        return self.library_dir / "manifest.json"

    @classmethod
    def from_env(cls) -> "Config":
        lib = os.environ.get("FORGER_LIBRARY")
        prov = os.environ.get("FORGER_PROVIDER") or "anthropic"
        lang = canonical_language(os.environ.get("FORGER_LANG"))
        return cls(
            library_dir=Path(lib) if lib else Path("./library"),
            provider=prov,
            session_language=lang,
            embed_provider=os.environ.get("FORGER_EMBED_PROVIDER"),
            embed_model=os.environ.get("FORGER_EMBED_MODEL"),
        )
