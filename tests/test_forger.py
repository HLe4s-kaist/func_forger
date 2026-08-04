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
from forger.agent import forge_documented, _format_lookup
from forger.spec import DefSpec, ModuleSpec


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
    assert module.definitions[0].name == "add"
    assert module.definitions[0].params == [("a", "int"), ("b", "int")]
    assert module.definitions[0].return_type == "int"


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
    spec = DefSpec(
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
    spec = DefSpec(name="double_sum", target_language="python", description="sum")
    hits = retrieve(spec, m)
    assert all(e.target_language == "python" for e in hits)


def test_retrieve_excludes_self():
    m = _lib_with_add()
    spec = DefSpec(name="add", target_language="python", description="sum")
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
    module = ModuleSpec(target_language="python", definitions=[DefSpec(name="double_sum")])
    implemented = parse(raw, module, {"add", "mul"})
    assert "def double_sum" in implemented.code
    assert implemented.used_names == ["add"]


def test_parse_rejects_missing_code():
    module = ModuleSpec(target_language="python", definitions=[DefSpec(name="f")])
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
        definitions=[DefSpec(name="double_sum", params=[("x", "int")], return_type="int")],
    )
    implemented = ImplementedModule(
        code="def double_sum(x, y):\n    return 2 * add(x, y)\n",
        used_names=["add"],
    )
    result = write_module(module, implemented, Path(d), m)

    assert (Path(d) / "python" / "double_sum.py").exists()
    assert result.entries[0].name == "double_sum"
    assert result.entries[0].depends_on == ["python:function:add"]
    # back-filled reverse edge
    assert "python:function:double_sum" in m.get("python", "add").imported_by
    assert result.unknown_deps == []


def test_write_module_flags_unknown_deps():
    d = tempfile.mkdtemp()
    m = Manifest(Path(d) / "manifest.json")
    module = ModuleSpec(target_language="python", definitions=[DefSpec(name="f")])
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


def test_same_name_different_kind_coexist():
    """A type and a function sharing a name must not silently overwrite each other."""
    d = tempfile.mkdtemp()
    m = Manifest(Path(d) / "manifest.json")
    m.upsert(ManifestEntry(name="Foo", target_language="c", kind="type",
        signature="struct Foo {}", file_path="c/a.c"))
    m.upsert(ManifestEntry(name="Foo", target_language="c", kind="function",
        signature="Foo()", file_path="c/b.c"))
    foos = [e for e in m.all() if e.name == "Foo"]
    assert len(foos) == 2
    assert {e.kind for e in foos} == {"type", "function"}
    # get() prefers the function when names collide
    assert m.get("c", "Foo").kind == "function"


# -- embedding search ------------------------------------------------------


class _BagOfWordsEmbedder:
    """Deterministic bag-of-words embedder for testing semantic ranking."""
    VOCAB = ["sum", "add", "integer", "integers", "two", "distance", "point",
             "euclidean", "max", "minimum", "string", "capitalize"]

    def available(self) -> bool:
        return True

    def _vec(self, text: str) -> list[float]:
        words = {w.strip(".,;:!?()") for w in (text or "").lower().split()}
        return [1.0 if w in words else 0.0 for w in self.VOCAB]

    def embed_query(self, query: str) -> list[float]:
        return self._vec(query)

    def embed_one(self, text: str) -> list[float]:
        return self._vec(text)


def test_embedding_search_ranks_add_for_sum_query():
    hits = search_library("sum two integers", _search_lib(), "python",
                          embedder=_BagOfWordsEmbedder())
    assert hits and hits[0].name == "add"


def test_search_falls_back_to_tokens_without_embedder():
    # No embedder -> token overlap path still works.
    hits = search_library("sum two integers", _search_lib(), "python")
    assert hits and hits[0].name == "add"


def test_null_embedder_is_unavailable():
    from forger.embeddings import NullEmbedder
    assert NullEmbedder().available() is False


# -- repo ingestion --------------------------------------------------------


def test_ingest_indexes_repo_files_with_real_paths():
    from forger.ingest import ingest

    root = Path(tempfile.mkdtemp())
    (root / "src").mkdir()
    (root / "src" / "add.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "src" / "math.c").write_text("int add(int a, int b){ return a+b; }\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("function junk() {}\n")  # excluded
    (root / "README.md").write_text("# readme\n")  # not source

    class IngestLLM:
        def complete(self, system, messages, *, model=None):
            content = messages[0]["content"]
            if "def add" in content:
                return ('{"language":"python","definitions":[{"name":"add",'
                        '"kind":"function","signature":"add(a, b)",'
                        '"description":"sum two values"}]}')
            if "int add" in content:
                return ('{"language":"c","definitions":[{"name":"add",'
                        '"kind":"function","signature":"int add(int, int)",'
                        '"description":"c sum"}]}')
            return '{"language":"x","definitions":[]}'

    cfg = Config()
    cfg.provider = "anthropic"
    manifest, files, defs = ingest(root, cfg, IngestLLM())
    assert files == 2, f"expected 2 source files, got {files}"
    assert defs == 2, defs
    # Same name, different languages -> coexist
    assert manifest.get("python", "add") is not None
    assert manifest.get("c", "add") is not None
    # file_path mirrors the repository's own structure
    assert manifest.get("python", "add").file_path == "src/add.py"
    assert manifest.get("c", "add").file_path == "src/math.c"
    # persisted to disk
    assert (root / "manifest.json").exists()


# -- agentic forge seeding -------------------------------------------------


def test_seed_includes_type_definitions():
    """The agent sees types/resources alongside functions (labeled with kind)."""
    d = tempfile.mkdtemp()
    m = Manifest(Path(d) / "manifest.json")
    m.upsert(ManifestEntry(
        name="Point", target_language="c", kind="type",
        signature="struct Point { float x; float y; }", description="2D point",
        file_path="c/geo/point.c"))
    captured = {}

    class SeedTypeLLM:
        def complete(self, system, messages, *, model=None):
            captured["msg"] = messages[0]["content"]
            return "```c\nfloat point_x(struct Point* p) { return p->x; }\n```\n"

    forge_documented("float point_x(struct Point* p);", "c", m, SeedTypeLLM())
    assert "[type]" in captured["msg"]
    assert "struct Point" in captured["msg"]


def test_forge_seeds_all_small_library():
    """A small library seeds EVERY same-language function so the agent always
    sees each one's exact signature (no search miss -> no wrong call)."""
    d = tempfile.mkdtemp()
    m = Manifest(Path(d) / "manifest.json")
    m.upsert(ManifestEntry(
        name="parse_csv", target_language="python", signature="parse_csv(s: str) -> list",
        description="parse csv", file_path="python/p.py"))
    m.upsert(ManifestEntry(
        name="multiply", target_language="python", signature="multiply(a: int, b: int) -> int",
        description="multiply", file_path="python/m.py"))
    captured = {}

    class SeedCaptureLLM:
        def complete(self, system, messages, *, model=None):
            captured["msg"] = messages[0]["content"]
            return "```python\ndef add(a, b):\n    return a + b\n```\n"

    forge_documented("def add(a, b): ...", "python", m, SeedCaptureLLM())
    # Both functions are textually unrelated to "add", yet must be seeded.
    assert "parse_csv" in captured["msg"]
    assert "multiply" in captured["msg"]


def test_format_lookup_returns_exact_signature():
    d = tempfile.mkdtemp()
    m = Manifest(Path(d) / "manifest.json")
    m.upsert(ManifestEntry(
        name="add", target_language="python", signature="add(a: int, b: int) -> int",
        params=[{"name": "a", "type": "int"}, {"name": "b", "type": "int"}],
        return_type="int", description="sum", doc="# add: returns a+b", file_path="python/a.py"))
    out = _format_lookup("add", m, "python")
    assert "signature: add(a: int, b: int) -> int" in out
    assert "returns: int" in out
    assert "kind: function" in out
    assert "not found" in _format_lookup("does_not_exist", m, "python")


def test_normalize_returns_kind_for_struct_and_function():
    llm = FakeLLM(
        json.dumps(
            {
                "language": "c",
                "category": "geometry",
                "module_name": "point",
                "definitions": [
                    {"name": "Point", "kind": "type", "signature": "struct Point { float x; float y; }",
                     "description": "2D point"},
                    {"name": "distance", "kind": "function", "params": [["a", "Point*"], ["b", "Point*"]],
                     "return_type": "float", "signature": "distance(a, b) -> float",
                     "description": "euclidean distance"},
                ],
            }
        )
    )
    module = normalize(
        "struct Point { float x; float y; };\nfloat distance(Point* a, Point* b);",
        InputKind.CODE, "c", llm,
    )
    kinds = {d.name: d.kind for d in module.definitions}
    assert kinds == {"Point": "type", "distance": "function"}
    assert module.target_language == "c"


def test_write_module_stores_kind_and_signature():
    d = tempfile.mkdtemp()
    m = Manifest(Path(d) / "manifest.json")
    module = ModuleSpec(
        target_language="c", module_name="point", category="geometry",
        definitions=[
            DefSpec(name="Point", kind="type", target_language="c",
                    signature="struct Point { float x; float y; }"),
            DefSpec(name="distance", kind="function", target_language="c", params=[("a", "Point*")]),
        ],
    )
    implemented = ImplementedModule(
        code="// 2D point.\nstruct Point { float x; float y; };\n"
             "// euclidean distance.\nfloat distance(Point* a, Point* b) { return 0; }\n",
        used_names=[],
    )
    res = write_module(module, implemented, Path(d), m)
    by_name = {e.name: e for e in res.entries}
    assert by_name["Point"].kind == "type"
    assert by_name["distance"].kind == "function"
    assert "struct Point" in by_name["Point"].signature
    assert by_name["Point"].doc  # doc extracted for the type too
    assert by_name["distance"].doc


def test_agent_uses_lookup_then_calls_correctly():
    d = tempfile.mkdtemp()
    m = Manifest(Path(d) / "manifest.json")
    m.upsert(ManifestEntry(
        name="add", target_language="python", signature="add(a: int, b: int) -> int",
        params=[{"name": "a", "type": "int"}, {"name": "b", "type": "int"}],
        return_type="int", description="sum two ints", doc="# add: returns a+b", file_path="python/a.py"))
    captured = []
    responses = iter([
        "Let me look up add first.\nLOOKUP: add\n",
        "```python\ndef inc(x):\n    return add(x, 1)\n```\n",
    ])

    class LookupLLM:
        def complete(self, system, messages, *, model=None):
            captured.append(messages)
            return next(responses)

    r = forge_documented("def inc(x): # use add to add 1", "python", m, LookupLLM())
    # The LOOKUP result (with the exact signature) was fed back to the agent.
    assert "add(a: int, b: int) -> int" in captured[-1][-1]["content"]
    assert "add(x, 1)" in r.code          # correct arity/order from the lookup
    assert r.used_names == ["add"]


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
