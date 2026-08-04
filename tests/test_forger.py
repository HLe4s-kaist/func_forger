"""Tests for Func-Forger.

Runs both under pytest and standalone (``python3 tests/test_forger.py``), so no
third-party test runner is required to verify the build.

The LLM is faked everywhere, which lets the whole forge pipeline run
deterministically without any network or API key.
"""

from __future__ import annotations

import json
import tempfile
import traceback
from pathlib import Path

from forger.config import Config, canonical_language
from forger.implementer import ImplementedModule, parse
from forger.input import InputKind, classify, normalize
from forger.library import write_module
from forger.manifest import Manifest, ManifestEntry
from forger.retrieve import retrieve, search_library
from forger.spec import FuncSpec, ModuleSpec


class FakeLLM:
    """Returns canned responses in order; records its calls."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system, messages, *, model=None):
        self.calls.append((system, messages))
        return self.responses.pop(0)


# -- classification --------------------------------------------------------


def test_classify_code():
    assert classify("def add(a: int, b: int) -> int: ...") is InputKind.CODE
    assert classify("int add(int a, int b);") is InputKind.CODE
    assert classify("fn add(a: i32, b: i32) -> i32 { }") is InputKind.CODE


def test_classify_natural_language():
    assert classify("a function that returns the max of two ints") is InputKind.NL
    assert classify("combine two strings with a separator") is InputKind.NL


def test_classify_file_path():
    d = tempfile.mkdtemp()
    p = Path(d) / "skel.py"
    p.write_text("def add(a, b): ...")
    assert classify(str(p)) is InputKind.FILE


# -- normalization ---------------------------------------------------------


def test_normalize_code_via_llm():
    llm = FakeLLM(
        json.dumps(
            {
                "language": "python",
                "module_name": "arith",
                "functions": [
                    {
                        "name": "add",
                        "params": [["a", "int"], ["b", "int"]],
                        "return_type": "int",
                        "description": "Return the sum of two integers.",
                    }
                ],
            }
        )
    )
    module = normalize("def add(a: int, b: int) -> int: ...", InputKind.CODE, None, llm)
    assert module.target_language == "python"
    assert module.module_name == "arith"
    assert module.functions[0].name == "add"
    assert module.functions[0].params == [("a", "int"), ("b", "int")]
    assert module.functions[0].return_type == "int"


def test_normalize_nl_requires_language():
    llm = FakeLLM("{}")
    try:
        normalize("a max function", InputKind.NL, None, llm)
        assert False, "expected LanguageRequired"
    except Exception as exc:
        assert "target language" in str(exc).lower()


# -- manifest --------------------------------------------------------------


def test_manifest_roundtrip():
    d = tempfile.mkdtemp()
    path = Path(d) / "manifest.json"
    m = Manifest(path)
    m.upsert(
        ManifestEntry(
            name="add",
            target_language="python",
            signature="add(a: int, b: int) -> int",
            file_path="python/arith.py",
        )
    )
    m.persist()

    reloaded = Manifest(path)
    assert reloaded.get("python", "add") is not None
    assert reloaded.get("python", "add").signature == "add(a: int, b: int) -> int"
    assert reloaded.get("python", "missing") is None


def test_manifest_upsert_replaces():
    d = tempfile.mkdtemp()
    m = Manifest(Path(d) / "manifest.json")
    m.upsert(ManifestEntry(name="add", target_language="python", signature="old", file_path="a.py"))
    m.upsert(ManifestEntry(name="add", target_language="python", signature="new", file_path="a.py"))
    assert len(m.all()) == 1
    assert m.get("python", "add").signature == "new"


# -- retrieval -------------------------------------------------------------


def _lib_with_add():
    d = tempfile.mkdtemp()
    m = Manifest(Path(d) / "manifest.json")
    m.upsert(
        ManifestEntry(
            name="add",
            target_language="python",
            signature="add(a: int, b: int) -> int",
            description="sum two ints",
            params=[{"name": "a", "type": "int"}, {"name": "b", "type": "int"}],
            return_type="int",
            file_path="python/arith.py",
        )
    )
    m.upsert(
        ManifestEntry(
            name="add",
            target_language="c",
            signature="int add(int,int)",
            file_path="c/arith.c",
        )
    )
    return m


def test_retrieve_finds_same_language_helper():
    m = _lib_with_add()
    spec = FuncSpec(
        name="double_sum",
        params=[("x", "int"), ("y", "int")],
        return_type="int",
        description="twice the sum of x and y",
        target_language="python",
    )
    hits = retrieve(spec, m)
    assert any(e.name == "add" and e.target_language == "python" for e in hits)


def test_retrieve_excludes_other_languages():
    m = _lib_with_add()
    spec = FuncSpec(name="double_sum", target_language="python", description="sum")
    hits = retrieve(spec, m)
    assert all(e.target_language == "python" for e in hits)


def test_retrieve_excludes_self():
    m = _lib_with_add()
    spec = FuncSpec(name="add", target_language="python", description="sum")
    hits = retrieve(spec, m)
    assert all(e.name != "add" or e.target_language != "python" for e in hits)


# -- implementer.parse -----------------------------------------------------


def test_parse_extracts_block_and_used_names():
    raw = (
        "Sure.\n"
        "```python\n"
        "def double_sum(x, y):\n"
        "    return 2 * add(x, y)\n"
        "```\n"
    )
    module = ModuleSpec(target_language="python", functions=[FuncSpec(name="double_sum")])
    implemented = parse(raw, module, {"add", "mul"})
    assert "def double_sum" in implemented.code
    assert implemented.used_names == ["add"]


def test_parse_rejects_missing_code():
    module = ModuleSpec(target_language="python", functions=[FuncSpec(name="f")])
    try:
        parse("no code here at all", module, set())
        assert False, "expected ValueError"
    except ValueError:
        pass


# -- library.write_module --------------------------------------------------


def test_write_module_and_imported_by():
    d = tempfile.mkdtemp()
    m = Manifest(Path(d) / "manifest.json")
    m.upsert(
        ManifestEntry(
            name="add",
            target_language="python",
            signature="add(a: int, b: int) -> int",
            file_path="python/arith.py",
        )
    )
    module = ModuleSpec(
        target_language="python",
        functions=[FuncSpec(name="double_sum", params=[("x", "int")], return_type="int")],
    )
    implemented = ImplementedModule(
        code="def double_sum(x, y):\n    return 2 * add(x, y)\n",
        used_names=["add"],
    )
    result = write_module(module, implemented, Path(d), m)

    assert (Path(d) / "python" / "double_sum.py").exists()
    assert result.entries[0].name == "double_sum"
    assert result.entries[0].depends_on == ["python:add"]
    # back-filled reverse edge
    assert "python:double_sum" in m.get("python", "add").imported_by
    assert result.unknown_deps == []


def test_write_module_flags_unknown_deps():
    d = tempfile.mkdtemp()
    m = Manifest(Path(d) / "manifest.json")
    module = ModuleSpec(target_language="python", functions=[FuncSpec(name="f")])
    implemented = ImplementedModule(code="def f():\n    return ghost()\n", used_names=["ghost"])
    result = write_module(module, implemented, Path(d), m)
    assert result.unknown_deps == ["ghost"]


# -- config ----------------------------------------------------------------


def test_canonical_language():
    assert canonical_language("py") == "python"
    assert canonical_language("TS") == "typescript"
    assert canonical_language("rs") == "rust"
    assert canonical_language(None) is None


def test_config_resolves_model():
    cfg = Config()
    cfg.provider = "anthropic"
    assert cfg.resolved_model()  # non-empty default


# -- full pipeline via FakeLLM --------------------------------------------


def test_full_forge_pipeline():
    cfg = Config()
    cfg.library_dir = Path(tempfile.mkdtemp())
    cfg.provider = "anthropic"

    from forger.repl import REPL

    repl = REPL(cfg)
    repl.llm = FakeLLM(
        # 1) normalize response
        json.dumps(
            {
                "language": "python",
                "module_name": "arith",
                "functions": [
                    {
                        "name": "add",
                        "params": [["a", "int"], ["b", "int"]],
                        "return_type": "int",
                        "description": "sum",
                    }
                ],
            }
        ),
        # 2) implement response
        "```python\ndef add(a, b):\n    return a + b\n```\n",
    )
    repl.cmd_forge("def add(a: int, b: int) -> int: ...")

    assert repl.manifest.get("python", "add") is not None
    assert (cfg.library_dir / "python" / "arith.py").read_text().strip() == "def add(a, b):\n    return a + b"


# -- search (free-text) ----------------------------------------------------


def _search_lib():
    d = tempfile.mkdtemp()
    m = Manifest(Path(d) / "manifest.json")
    m.upsert(ManifestEntry(
        name="add", target_language="python", signature="add(a: int, b: int) -> int",
        description="sum two integers", file_path="python/arith.py",
        doc="# Sum two integers.\n# Behavior: returns the arithmetic sum of a and b.",
    ))
    m.upsert(ManifestEntry(
        name="double_sum", target_language="python",
        signature="double_sum(x: int, y: int) -> int", description="double the sum",
        file_path="python/arith.py", doc="# Returns twice the sum, via add.",
    ))
    m.upsert(ManifestEntry(
        name="capitalize", target_language="python",
        signature="capitalize(s: str) -> str", description="uppercase the first letter",
        file_path="python/strings.py", doc="# Capitalize a string.",
    ))
    m.upsert(ManifestEntry(
        name="add", target_language="c", signature="int add(int, int)",
        file_path="c/arith.c",
    ))
    return m


def test_search_finds_add_for_sum_query():
    hits = search_library("sum two integers", _search_lib(), "python")
    assert hits and hits[0].name == "add"


def test_search_finds_double_sum():
    hits = search_library("double the sum", _search_lib(), "python")
    assert hits and hits[0].name == "double_sum"


def test_search_excludes_other_languages():
    hits = search_library("add integers", _search_lib(), "python")
    assert hits and all(e.target_language == "python" for e in hits)


def test_search_empty_query_returns_empty():
    assert search_library("   ", _search_lib(), "python") == []


def test_search_camelcase_identifier_split():
    d = tempfile.mkdtemp()
    m = Manifest(Path(d) / "manifest.json")
    m.upsert(ManifestEntry(
        name="parseCSV", target_language="python", signature="parseCSV(s) -> list",
        description="parse csv data", file_path="python/csv.py",
        doc="# parse comma-separated values",
    ))
    hits = search_library("csv parsing", m, "python")
    assert hits and hits[0].name == "parseCSV"


# -- standalone runner -----------------------------------------------------


def _run_all() -> int:
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            failures += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    total = len(tests)
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
