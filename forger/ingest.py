"""Ingest an existing repository into a SEPARATE index, leaving the repo untouched.

Point Func-Forger at an existing codebase so its definitions become first-class,
searchable, reusable library entries -- then forge new functions that compose
the ones already there.

The repository is treated as READ-ONLY: source files are only read. The index
(manifest + per-file fingerprints) lives OUTSIDE the repo, at
``~/.forger/repos/<slug>/`` (override with ``FORGER_INDEX_DIR``), so ingesting
never adds or modifies anything in your project.

    forger ingest ./my_repo            # read-only; index written to ~/.forger/...
    forger --library ./my_repo          # forge on top of the repo; the matching
                                        # index is found automatically

Incremental: a per-file fingerprint is kept, so re-running ``ingest`` only
re-analyzes files that changed (and drops entries for deleted files). The
analysis itself is done by the LLM (language-agnostic -- no per-language parser),
which means it is approximate and costs one LLM call per changed source file;
see the README for the caveats.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from forger.input import _extract_json
from forger.manifest import Manifest, ManifestEntry

# File extension -> language. This is just file ROUTING (which files to scan);
# the actual definition extraction is done by the LLM, so there is no per-language
# syntax parser.
_EXT_LANG = {
    ".py": "python", ".pyx": "python",
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

_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env", ".env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "build", "dist", "target", "out", ".next", ".nuxt", "vendor",
    ".idea", ".vscode", ".forger", "site-packages",
}

_MAX_FILE_BYTES = 200_000

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


def _index_base() -> Path:
    override = os.environ.get("FORGER_INDEX_DIR")
    return Path(override) if override else Path.home() / ".forger"


def index_dir_for(repo_dir: str | Path) -> Path:
    """Where a repo's read-only index lives (outside the repo)."""
    slug = hashlib.sha1(str(Path(repo_dir).resolve()).encode()).hexdigest()[:16]
    return _index_base() / "repos" / slug


def manifest_path_for(repo_dir: str | Path) -> Path:
    return index_dir_for(repo_dir) / "manifest.json"


def language_for_file(path: Path) -> str | None:
    return _EXT_LANG.get(path.suffix.lower())


def walk_source_files(repo_dir: Path, max_files: int | None = None) -> list[Path]:
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


def _fingerprint(path: Path) -> str:
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def _load_json(path: Path) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def ingest(
    repo_dir: str | Path,
    llm,
    max_files: int | None = None,
    on_progress=None,
) -> tuple[Manifest, int, int, int]:
    """Index ``repo_dir`` into its (separate, read-only) index.

    Returns ``(manifest, files_total, files_reanalyzed, definitions)``.
    Re-runs are incremental: only changed files are re-analyzed and deleted
    files' entries are dropped.
    """
    repo = Path(repo_dir)
    if not repo.is_dir():
        raise NotADirectoryError(f"{repo} is not a directory")
    index_dir = index_dir_for(repo)
    index_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(index_dir / "manifest.json")
    state_path = index_dir / "files.json"
    prev_state = _load_json(state_path)

    files = walk_source_files(repo, max_files=max_files)
    new_state: dict[str, str] = {}
    seen: set[str] = set()
    reanalyzed = 0

    for index, path in enumerate(files, start=1):
        rel = path.relative_to(repo).as_posix()
        seen.add(rel)
        fingerprint = _fingerprint(path)
        new_state[rel] = fingerprint

        if prev_state.get(rel) == fingerprint:
            if on_progress:
                on_progress(index, len(files), rel, -1)  # unchanged -> skipped
            continue

        # Changed/new: drop this file's old entries, then re-analyze.
        for entry in [e for e in manifest.all() if e.file_path == rel]:
            manifest.entries.pop(entry.id, None)
        reanalyzed += 1
        language = language_for_file(path) or "python"
        try:
            code = path.read_text(encoding="utf-8", errors="ignore")
            data = _extract_json(
                llm.complete(
                    INGEST_PROMPT,
                    [{"role": "user", "content": f"Target language: {language}\n\nSource file:\n```\n{code}\n```"}],
                )
            )
        except Exception:
            if on_progress:
                on_progress(index, len(files), rel, 0)
            continue

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
        if on_progress:
            on_progress(index, len(files), rel, added)

    # Drop entries whose source file no longer exists.
    for entry in [e for e in manifest.all() if e.file_path not in seen]:
        manifest.entries.pop(entry.id, None)

    manifest.persist()
    state_path.write_text(json.dumps(new_state, indent=2))
    return manifest, len(files), reanalyzed, len(manifest.all())
