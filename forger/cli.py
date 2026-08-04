"""Command-line entry point.

Launches the two-pane TUI by default. ``--repl`` selects the legacy
conversational REPL. Configuration precedence everywhere is: explicit REPL
value > CLI flag > environment variable > built-in default.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from forger.config import Config, canonical_language


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forger",
        description=(
            "Human drafts function skeletons; an LLM implements them with "
            "rustdoc-grade docs, stores them in a library, and reuses them "
            "when building new ones."
        ),
    )
    parser.add_argument(
        "--repl",
        action="store_true",
        help="use the legacy conversational REPL instead of the TUI",
    )
    parser.add_argument("--library", "-l", help="library directory (default: ./library)")
    parser.add_argument("--provider", help="LLM backend: anthropic | openai-compat")
    parser.add_argument("--model", help="model id (e.g. glm-4.6, claude-sonnet-4-6, gpt-4o-mini)")
    parser.add_argument("--base-url", dest="base_url", help="API base URL")
    parser.add_argument("--lang", help="default target language for natural-language input")
    parser.add_argument(
        "--embed-provider",
        help="semantic search backend: none | fastembed | sentence-transformers",
    )
    parser.add_argument("--embed-model", help="local embedding model name")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = Config.from_env()
    if args.library:
        config.library_dir = Path(args.library)
    if args.provider:
        config.provider = args.provider
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.lang:
        config.session_language = canonical_language(args.lang)
    if args.embed_provider:
        config.embed_provider = args.embed_provider
    if args.embed_model:
        config.embed_model = args.embed_model

    if args.repl:
        from forger.repl import REPL

        REPL(config).run()
    else:
        from forger.tui import ForgeApp

        ForgeApp(config).run()


if __name__ == "__main__":
    main()
