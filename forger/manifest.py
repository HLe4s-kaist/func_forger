"""The library index (``manifest.json``).

The manifest lives at the root of the library directory and records every
implemented function so the agent can discover and reuse them -- this is the
substrate for "fast pattern matching". Each entry has an id of the form
``<language>:<name>``; a function implemented in two languages is two entries.

Updates are a full read / modify / atomic-write cycle. The REPL is
single-threaded so no locking is required.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ManifestEntry:
    """One implemented function in the library."""

    name: str
    target_language: str
    signature: str
    file_path: str
    description: str = ""
    params: list[dict] = field(default_factory=list)
    return_type: str | None = None
    depends_on: list[str] = field(default_factory=list)
    imported_by: list[str] = field(default_factory=list)
    created_at: str = ""

    @property
    def id(self) -> str:
        return f"{self.target_language}:{self.name}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "target_language": self.target_language,
            "signature": self.signature,
            "params": self.params,
            "return_type": self.return_type,
            "description": self.description,
            "file_path": self.file_path,
            "depends_on": self.depends_on,
            "imported_by": self.imported_by,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ManifestEntry":
        return cls(
            name=d["name"],
            target_language=d["target_language"],
            signature=d["signature"],
            file_path=d["file_path"],
            description=d.get("description", ""),
            params=d.get("params", []),
            return_type=d.get("return_type"),
            depends_on=d.get("depends_on", []),
            imported_by=d.get("imported_by", []),
            created_at=d.get("created_at", ""),
        )


class Manifest:
    """In-memory view of ``manifest.json`` with load / query / upsert / persist."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.entries: dict[str, ManifestEntry] = {}
        self.load()

    def load(self) -> None:
        self.entries = {}
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Manifest at {self.path} is corrupt and unreadable: {e}") from e
        for d in data.get("functions", []):
            entry = ManifestEntry.from_dict(d)
            self.entries[entry.id] = entry

    def all(self) -> list[ManifestEntry]:
        return list(self.entries.values())

    def get(self, language: str, name: str) -> ManifestEntry | None:
        return self.entries.get(f"{language}:{name}")

    def upsert(self, entry: ManifestEntry) -> None:
        """Insert or replace an entry keyed by its id."""
        self.entries[entry.id] = entry

    def persist(self) -> None:
        """Atomically write the manifest (tmp file + ``os.replace``)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "functions": [e.to_dict() for e in self.entries.values()]}
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
