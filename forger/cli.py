"""Command-line entry point.

Launches the two-pane TUI by default. ``--repl`` selects the legacy
conversational REPL. Configuration precedence everywhere is: explicit REPL
value > CLI flag > environment variable > built-in default.
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
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == "ingest":
        return _run_ingest(args[1:])

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

    if ns.repl:
        from forger.repl import REPL

        REPL(config).run()
    else:
        from forger.tui import ForgeApp

        ForgeApp(config).run()


def _run_ingest(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="forger ingest",
        description="Index an existing repository into a Func-Forger library manifest.",
    )
    parser.add_argument("repo_dir", help="path to the repository to index")
    parser.add_argument("--provider", help="LLM backend: anthropic | openai-compat")
    parser.add_argument("--model", help="model id")
    parser.add_argument("--base-url", dest="base_url", help="API base URL")
    parser.add_argument("--lang", help="default target language (fallback)")
    parser.add_argument("--max-files", type=int, default=None, help="stop after this many files")
    ns = parser.parse_args(argv)

    config = Config.from_env()
    if ns.provider:
        config.provider = ns.provider
    if ns.model:
        config.model = ns.model
    if ns.base_url:
        config.base_url = ns.base_url
    if ns.lang:
        config.session_language = canonical_language(ns.lang)

    from forger.ingest import ingest
    from forger.llm import make_provider

    llm = make_provider(config)

    def progress(done: int, total: int, rel: str, added: int) -> None:
        print(f"  [{done}/{total}] {rel}  (+{added} definition{'s' if added != 1 else ''})")

    manifest, files, defs = ingest(
        ns.repo_dir, config, llm, max_files=ns.max_files, on_progress=progress
    )
    print(f"Indexed {files} file(s), {defs} definition(s) -> {Path(ns.repo_dir) / 'manifest.json'}")
    print("Now forge against it:  forger --library " + ns.repo_dir)


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
