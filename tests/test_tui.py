"""Headless tests for the Func-Forger TUI.

Deterministic (FakeLLM) so they run without a network or API key. Uses Textual's
``run_test`` pilot. Run standalone (``python3 tests/test_tui.py``) -- requires the
``textual`` dependency, i.e. the project venv.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import traceback
from pathlib import Path

from forger.agent import regen_range
from forger.config import Config
from forger.tui import ForgeApp


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)

    def complete(self, system, messages, *, model=None):
        return self.responses.pop(0)


def _config():
    cfg = Config()
    cfg.library_dir = Path(tempfile.mkdtemp())
    cfg.provider = "anthropic"
    cfg.session_language = "python"
    return cfg


def test_tui_forge_approve_indexes_doc():
    async def run():
        cfg = _config()
        app = ForgeApp(cfg)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#editor")
            app.llm = FakeLLM(
                json.dumps(
                    {
                        "language": "python",
                        "module_name": "arith",
                        "functions": [
                            {
                                "name": "add",
                                "params": [["a", "int"], ["b", "int"]],
                                "return_type": "int",
                                "description": "sum two integers",
                            }
                        ],
                    }
                ),
                "```python\n# Sum two integers.\n# Args: a (int), b (int)\n"
                "# Returns: int\n# Behavior: returns a + b.\n"
                "def add(a, b):\n    return a + b\n```\n",
            )
            app.query_one("#editor").text = "def add(a: int, b: int) -> int: ..."
            app.action_forge()
            for _ in range(300):
                await pilot.pause(0.02)
                if app.state == "review":
                    break
            assert app.state == "review", f"state={app.state}"
            assert "def add" in app.query_one("#editor").text

            app.action_approve()
            await pilot.pause(0.05)
            entry = app.manifest.get("python", "add")
            assert entry is not None
            assert entry.doc and "Args" in entry.doc
            assert (cfg.library_dir / entry.file_path).exists()
            assert app.query_one("#funclist").option_count >= 1

    asyncio.run(run())


def test_tui_reject_restores_skeleton():
    async def run():
        cfg = _config()
        app = ForgeApp(cfg)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.llm = FakeLLM(
                json.dumps(
                    {"language": "python", "module_name": "m", "functions": [
                        {"name": "f", "params": [], "return_type": None, "description": "x"}]}
                ),
                "```python\ndef f():\n    return 1\n```\n",
            )
            skel = "def f(): ..."
            app.query_one("#editor").text = skel
            app.action_forge()
            for _ in range(300):
                await pilot.pause(0.02)
                if app.state == "review":
                    break
            app.action_reject()
            await pilot.pause(0.02)
            assert app.state == "entry"
            assert app.query_one("#editor").text == skel
            assert app.manifest.get("python", "f") is None

    asyncio.run(run())


def test_regen_range_keeps_rest():
    llm = FakeLLM(
        "```python\ndef f():\n    return 2\n```\n",
    )
    new_code = regen_range("def f():\n    return 1\n", "return 1", "return 2 instead", "python", llm)
    assert "def f" in new_code
    assert "2" in new_code


def test_external_editor_does_not_crash_headless():
    async def run():
        os.environ["EDITOR"] = "true"
        cfg = _config()
        app = ForgeApp(cfg)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#editor").text = "def f(): ..."
            app.action_external_editor()  # suspend is unsupported in headless; must not crash
            await pilot.pause(0.05)
            assert app.state == "entry"

    asyncio.run(run())


def _run_all() -> int:
    tests = [
        (n, fn) for n, fn in sorted(globals().items())
        if n.startswith("test_") and callable(fn)
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
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
