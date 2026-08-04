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
        return

    # TUI: auto-index the codebase on first run (no manifest yet + source present).
    _maybe_auto_ingest(config)
    from forger.tui import ForgeApp

    ForgeApp(config).run()


def _maybe_auto_ingest(config: Config) -> None:
    """If the library has source files but no manifest yet, index them now."""
    if config.manifest_path.exists():
        return
    from forger.ingest import has_source_files, ingest
    from forger.llm import make_provider

    if not has_source_files(config.library_dir):
        return
    print(f"No index yet at {config.manifest_path}; indexing existing source...")
    ingest(
        config.library_dir,
        make_provider(config),
        on_progress=lambda i, n, rel, added: print(f"  [{i}/{n}] {rel} (+{added})"),
    )


def _run_ingest(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="forger ingest",
        description="Index a codebase into its manifest (full rebuild).",
    )
    parser.add_argument("repo_dir", help="directory to index")
    parser.add_argument("--provider", help="LLM backend: anthropic | openai-compat")
    parser.add_argument("--model", help="model id")
    parser.add_argument("--base-url", dest="base_url", help="API base URL")
    parser.add_argument("--max-files", type=int, default=None, help="stop after this many files")
    ns = parser.parse_args(argv)

    config = Config.from_env()
    if ns.provider:
        config.provider = ns.provider
    if ns.model:
        config.model = ns.model
    if ns.base_url:
        config.base_url = ns.base_url

    from forger.ingest import ingest
    from forger.llm import make_provider

    llm = make_provider(config)

    def progress(done: int, total: int, rel: str, added: int) -> None:
        print(f"  [{done}/{total}] {rel}  (+{added} definition{'s' if added != 1 else ''})")

    manifest, files, defs = ingest(ns.repo_dir, llm, max_files=ns.max_files, on_progress=progress)
    print(f"Indexed {files} file(s), {defs} definition(s) -> {Path(ns.repo_dir) / 'manifest.json'}")
    print("Now forge on it:  forger --library " + ns.repo_dir)


if __name__ == "__main__":
    main()
