"""Ingest: index a codebase so its existing definitions are searchable/reusable.

Func-Forger works directly on the library/codebase directory you point it at --
it is NOT read-only (back up your original first; see the README). On first run
it auto-indexes the existing source; `forger ingest <dir>` forces a full
re-index (e.g. after you've edited a lot of files).

The index is a single ``<dir>/manifest.json``. The analysis is done by the LLM
(language-agnostic -- no per-language parser), so it is approximate and costs
one LLM call per source file.
"""

from __future__ import annotations

import re
from pathlib import Path

from forger.input import _extract_json
from forger.library import _extract_doc
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
    ".idea", ".vscode", "site-packages",
}

def _read_source(path: Path) -> str | None:
    """Read a source file as text with encoding detection.

    Order: BOM → UTF-8 strict → charset-normalizer (auto-installed, handles
    UTF-16, GBK, Shift-JIS, EUC-KR, Big5, etc.). Returns None if binary.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None

    # BOM detection.
    if raw.startswith(b"\xef\xbb\xbf"):  # UTF-8 BOM
        try:
            return raw[3:].decode("utf-8")
        except UnicodeDecodeError:
            pass
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):  # UTF-16 LE/BE BOM
        try:
            return raw.decode("utf-16")
        except (UnicodeDecodeError, LookupError):
            pass

    # UTF-8 strict (the vast majority of modern source).
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # Non-UTF-8: use charset detection (auto-installed, lightweight pure-Python).
    try:
        from charset_normalizer import from_bytes
    except ImportError:
        import subprocess
        import sys
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "charset-normalizer"],
                check=True, timeout=60,
            )
            from charset_normalizer import from_bytes
        except Exception:
            return None

    result = from_bytes(raw).best()
    return str(result) if result else None


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


def walk_source_files(library_dir: Path, max_files: int | None = None) -> list[Path]:
    files: list[Path] = []
    for path in library_dir.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _EXCLUDE_DIRS for part in path.relative_to(library_dir).parts[:-1]):
            continue
        if language_for_file(path) is None:
            continue
        files.append(path)
        if max_files and len(files) >= max_files:
            break
    return sorted(files)


def has_source_files(library_dir: str | Path) -> bool:
    """Cheap check: does this directory contain any indexable source file?"""
    return bool(walk_source_files(Path(library_dir), max_files=1))


def ingest(
    library_dir: str | Path,
    llm,
    max_files: int | None = None,
    on_progress=None,
) -> tuple[Manifest, int, int]:
    """Index ``library_dir`` into ``<library_dir>/manifest.json`` (full rebuild).

    Returns ``(manifest, files_total, definitions)``.
    """
    root = Path(library_dir)
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")
    manifest = Manifest(root / "manifest.json")
    manifest.entries.clear()  # full rebuild each run
    file_codes: dict[str, str] = {}  # rel path -> source, for doc/graph rebuild

    files = walk_source_files(root, max_files=max_files)
    defs_total = 0
    for index, path in enumerate(files, start=1):
        rel = path.relative_to(root).as_posix()
        language = language_for_file(path) or "python"
        # Text vs binary: try common encodings; skip if none decode.
        code = _read_source(path)
        if code is None:
            if on_progress:
                on_progress(index, len(files), rel, 0)  # binary, skipped
            continue
        file_codes[rel] = code
        try:
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
                    # Re-extract the rustdoc/comment block from the actual source
                    # so re-indexing preserves the rich docs (not just the one-liner).
                    doc=_extract_doc(code, str(name)),
                    file_path=rel,
                )
            )
            added += 1
        defs_total += added
        if on_progress:
            on_progress(index, len(files), rel, added)

    # Rebuild the dependency graph from source so it survives re-indexing.
    _rebuild_graph(manifest, file_codes)

    manifest.persist()
    return manifest, len(files), defs_total


def _rebuild_graph(manifest: Manifest, file_codes: dict[str, str]) -> None:
    """Recompute depends_on / imported_by by scanning each file's source."""
    by_lang: dict[str, dict[str, str]] = {}
    for entry in manifest.all():
        by_lang.setdefault(entry.target_language, {})[entry.name] = entry.id

    for entry in manifest.all():
        code = file_codes.get(entry.file_path, "")
        deps: list[str] = []
        for name, dep_id in by_lang.get(entry.target_language, {}).items():
            if name == entry.name:
                continue
            if re.search(rf"\b{re.escape(name)}\b", code):
                deps.append(dep_id)
        entry.depends_on = deps
        entry.imported_by = []

    for entry in manifest.all():
        for dep_id in entry.depends_on:
            dep = manifest.entries.get(dep_id)
            if dep and entry.id not in dep.imported_by:
                dep.imported_by.append(entry.id)
