"""Command-line entry point.

Launches the two-pane TUI by default (auto-indexing the library on first run).
``--repl`` selects the legacy conversational REPL; ``forger ingest <dir>``
forces a full re-index. Configuration precedence is: explicit REPL value >
CLI flag > environment variable > built-in default.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from forger.config import Config, canonical_language


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forger",
        description=(
            "Human drafts skeletons; an LLM implements them with rustdoc-grade "
            "docs, files them in a library, and reuses existing definitions."
        ),
    )
    parser.add_argument("--repl", action="store_true", help="legacy conversational REPL")
    parser.add_argument("--library", "-l", help="library/codebase directory (default: ./library)")
    parser.add_argument("--provider", help="LLM backend: anthropic | openai-compat")
    parser.add_argument("--model", help="model id")
    parser.add_argument("--base-url", dest="base_url", help="API base URL")
    parser.add_argument("--lang", help="default target language")
    parser.add_argument(
        "--embed-provider", help="semantic search: none | fastembed | sentence-transformers"
    )
    parser.add_argument("--embed-model", help="local embedding model name")
    parser.add_argument(
        "--embed",
        action="store_true",
        help="enable semantic search (on by default; installed via pip install -e .)",
    )
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="disable semantic search (use token search only)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else list(argv)
    ns = build_parser().parse_args(args)
    config = Config.from_env()
    if ns.library:
        config.library_dir = Path(ns.library)
    if ns.provider:
        config.provider = ns.provider
    if ns.model:
        config.model = ns.model
    if ns.base_url:
        config.base_url = ns.base_url
    if ns.lang:
        config.session_language = canonical_language(ns.lang)
    if ns.embed_provider:
        config.embed_provider = ns.embed_provider
    if ns.embed_model:
        config.embed_model = ns.embed_model
    if ns.embed and not config.embed_provider:
        config.embed_provider = "fastembed"  # enable semantic search explicitly
    if ns.no_embed:
        config.embed_provider = "none"

    if ns.repl:
        from forger.repl import REPL

        REPL(config).run()
        return

    # TUI: first-time integration — index the existing source only when there is
    # no manifest yet. After that the manifest persists; re-indexing later is a
    # deliberate action (delete manifest.json and relaunch).
    _maybe_auto_ingest(config)
    from forger.tui import ForgeApp

    ForgeApp(config).run()


def _maybe_auto_ingest(config: Config) -> None:
    """One-time: index the library's existing source if no manifest exists yet."""
    if config.manifest_path.exists():
        return
    from forger.ingest import has_source_files, ingest
    from forger.llm import make_provider

    if not has_source_files(config.library_dir):
        return
    print(f"No index yet at {config.manifest_path}; indexing existing source (one-time)...")
    ingest(
        config.library_dir,
        make_provider(config),
        on_progress=lambda i, n, rel, added: print(f"  [{i}/{n}] {rel} (+{added})"),
    )


if __name__ == "__main__":
    main()
