"""Ingest an existing repository into the library manifest.

Point Func-Forger at an existing codebase so its definitions become first-class,
searchable, reusable library entries -- then forge new functions that compose
the ones already there.

How it works (language-agnostic -- the LLM does the analysis, no per-language
parser):
    for each source file (selected by extension, excluding noise dirs)
        -> the LLM lists its top-level definitions (name, kind, signature)
           and writes a one-line description for each (indexed for search)
    -> each becomes a ManifestEntry whose file_path is the file's real path in
       the repo, so the sidebar tree mirrors the repository's own structure.

Then run the TUI against the same directory and forge as usual:

    forger ingest ./my_repo
    forger --library ./my_repo
"""

from __future__ import annotations

from pathlib import Path

from forger.config import Config
from forger.input import _extract_json
from forger.manifest import Manifest, ManifestEntry

# File extension -> language. This is just file ROUTING (which files to scan);
# the actual definition extraction is done by the LLM, so there is no per-language
# syntax parser.
_EXT_LANG = {
    ".py": "python",
    ".pyx": "python",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".rs": "rust",
    ".go": "go",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".swift": "swift",
    ".cs": "csharp",
    ".rb": "ruby",
    ".lua": "lua",
    ".zig": "zig",
    ".ml": "ocaml",
    ".ex": "elixir", ".exs": "elixir",
}

# Directories that never hold project source we want to index.
_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env", ".env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "build", "dist", "target", "out", ".next", ".nuxt", "vendor",
    ".idea", ".vscode", ".forger", "site-packages",
}

_MAX_FILE_BYTES = 200_000  # skip files larger than this (the LLM call would be huge)

INGEST_PROMPT = """\
You inspect ONE source file and list its top-level DEFINITIONS for a code \
library's index: functions, structs/classes/traits/enums, type aliases/typedefs, \
macros, and module-level constants/globals. For EACH give: name; kind (the \
closest of function, type, typedef, macro, constant); a concise signature \
string; and a one-line description of what it is/does -- this is indexed for \
search, so be specific and use searchable words. Ignore imports, local \
variables, and purely private helpers unless they are meaningful top-level \
definitions. Respond with ONLY a JSON object of this shape and nothing else: \
{"language":"...","definitions":[{"name":"...","kind":"function",\
"signature":"...","description":"..."}]}. If the file defines nothing \
meaningful, return {"language":"...","definitions":[]}.
"""


def language_for_file(path: Path) -> str | None:
    return _EXT_LANG.get(path.suffix.lower())


def walk_source_files(repo_dir: Path, max_files: int | None = None) -> list[Path]:
    """Source files under ``repo_dir``, skipping noise dirs and oversized files."""
    files: list[Path] = []
    for path in repo_dir.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _EXCLUDE_DIRS for part in path.relative_to(repo_dir).parts[:-1]):
            continue
        if language_for_file(path) is None:
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        files.append(path)
        if max_files and len(files) >= max_files:
            break
    return sorted(files)


def ingest(
    repo_dir: str | Path,
    config: Config,
    llm,
    max_files: int | None = None,
    on_progress=None,
) -> tuple[Manifest, int, int]:
    """Index ``repo_dir`` into its manifest. Returns (manifest, files_done, defs)."""
    repo = Path(repo_dir)
    if not repo.is_dir():
        raise NotADirectoryError(f"{repo} is not a directory")
    manifest = Manifest(repo / "manifest.json")
    files = walk_source_files(repo, max_files=max_files)

    defs_total = 0
    for index, path in enumerate(files, start=1):
        language = language_for_file(path) or "python"
        try:
            code = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        try:
            data = _extract_json(
                llm.complete(
                    INGEST_PROMPT,
                    [{"role": "user", "content": f"Target language: {language}\n\nSource file:\n```\n{code}\n```"}],
                )
            )
        except Exception:
            # A single unreadable file shouldn't abort the whole ingestion.
            if on_progress:
                on_progress(index, len(files), path.relative_to(repo).as_posix(), 0)
            continue

        rel = path.relative_to(repo).as_posix()
        file_language = (data.get("language") or language).strip().lower() or language
        added = 0
        for definition in data.get("definitions", []):
            name = definition.get("name")
            if not name:
                continue
            manifest.upsert(
                ManifestEntry(
                    name=str(name),
                    target_language=file_language,
                    kind=(definition.get("kind") or "function").strip().lower() or "function",
                    signature=definition.get("signature") or str(name),
                    description=definition.get("description") or "",
                    file_path=rel,
                )
            )
            added += 1
        defs_total += added
        if on_progress:
            on_progress(index, len(files), rel, added)

    manifest.persist()
    return manifest, len(files), defs_total
