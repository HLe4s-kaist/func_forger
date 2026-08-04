"""Write implemented modules to disk and produce manifest entries.

Layout (simple and deterministic for v1; LLM-driven restructuring is a later
feature and is enabled by storing ``file_path`` per entry)::

    <library_dir>/
        manifest.json
        python/<module>.py
        typescript/<module>.ts
        c/<module>.c
        ...

All functions of one :class:`~forger.spec.ModuleSpec` land in the same module
file because they were provided together and are related by intent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from forger.implementer import ImplementedModule
from forger.manifest import Manifest, ManifestEntry
from forger.spec import ModuleSpec

EXTENSIONS = {
    "python": ".py",
    "typescript": ".ts",
    "javascript": ".js",
    "c": ".c",
    "cpp": ".cpp",
    "rust": ".rs",
    "go": ".go",
    "java": ".java",
}


@dataclass
class WriteResult:
    entries: list[ManifestEntry]
    rel_path: str
    unknown_deps: list[str]  # referenced names not present in the library


def ext_for(language: str) -> str:
    return EXTENSIONS.get(language, ".txt")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()
    return slug or "module"


def _signature(name: str, params: list[tuple[str, str]], return_type: str | None) -> str:
    params_str = ", ".join(f"{n}: {t}" for n, t in params)
    ret = f" -> {return_type}" if return_type else ""
    return f"{name}({params_str}){ret}"


_DEF_WORDS = (
    r"(?:def|fn|func|function|pub|public|private|protected|static|void|int|"
    r"float|double|char|long|short|auto|const|let|var|impl|inline|unsigned|"
    r"signed|size_t|string|bool)"
)


def _extract_doc(code: str, name: str) -> str:
    """Best-effort extraction of the rustdoc-style block above a function.

    Falls back to the whole module source so the search index always has rich,
    searchable text even when the heuristic cannot pinpoint a block.
    """
    lines = code.splitlines()
    def_line = None
    for i, line in enumerate(lines):
        # First line that mentions the definition name as a whole word. Works for
        # any kind (function, struct, typedef, macro, ...) without per-language
        # keyword knowledge.
        if re.search(rf"\b{re.escape(name)}\b", line):
            def_line = i
            break

    block: list[str] = []
    if def_line is not None:
        j = def_line - 1
        while j >= 0:
            stripped = lines[j].strip()
            if not stripped:
                break
            if stripped.startswith(("#", "//", "/*", "*/", "*", '"""', "'''")):
                block.append(lines[j])
                j -= 1
            else:
                break
        block.reverse()
        # Python triple-quoted docstring immediately inside the body.
        k = def_line + 1
        if k < len(lines):
            s = lines[k].strip()
            if s.startswith(('"""', "'''")):
                quote = s[:3]
                block.append(lines[k])
                if s.count(quote) < 2:
                    k += 1
                    while k < len(lines) and quote not in lines[k]:
                        block.append(lines[k])
                        k += 1
                    if k < len(lines):
                        block.append(lines[k])

    doc = "\n".join(block).strip()
    return doc or code.strip()


def write_module(
    module: ModuleSpec,
    implemented: ImplementedModule,
    library_dir: Path,
    manifest: Manifest,
) -> WriteResult:
    """Persist ``implemented.code`` to disk and build manifest entries.

    Also back-fills ``imported_by`` on the dependency entries in ``manifest``
    so the dependency graph stays accurate. The caller is responsible for
    ``manifest.persist()``.
    """
    language = module.target_language
    base_name = module.module_name or module.definitions[0].name
    slug = _slugify(base_name)
    parts = [language]
    if module.category:
        parts.append(_slugify(module.category))
    rel_path = Path(*parts) / f"{slug}{ext_for(language)}"
    abs_path = library_dir / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(implemented.code, encoding="utf-8")

    now = datetime.now(timezone.utc).isoformat()
    known_used_ids: list[str] = []
    unknown_deps: list[str] = []
    for name in implemented.used_names:
        if manifest.get(language, name):
            dep_id = f"{language}:{name}"
            if dep_id not in known_used_ids:
                known_used_ids.append(dep_id)
        elif name not in unknown_deps:
            unknown_deps.append(name)

    entries: list[ManifestEntry] = []
    for fn in module.definitions:
        entries.append(
            ManifestEntry(
                name=fn.name,
                target_language=language,
                kind=fn.kind,
                signature=fn.signature or _signature(fn.name, fn.params, fn.return_type),
                file_path=str(rel_path),
                description=fn.description or "",
                doc=_extract_doc(implemented.code, fn.name),
                params=[{"name": n, "type": t} for n, t in fn.params],
                return_type=fn.return_type,
                depends_on=list(known_used_ids),
                imported_by=[],
                created_at=now,
            )
        )

    for entry in entries:
        for dep_id in entry.depends_on:
            dep = manifest.entries.get(dep_id)
            if dep and entry.id not in dep.imported_by:
                dep.imported_by.append(entry.id)

    return WriteResult(entries=entries, rel_path=str(rel_path), unknown_deps=unknown_deps)
