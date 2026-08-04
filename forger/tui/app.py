"""The Func-Forger Textual TUI.

Layout:
    +-------------------------------+----------------+
    |                               | search [____]  |
    |         editor (TextArea)     | functions      |
    |         skeleton <-> code     |   ...          |
    |                               | files          |
    |                               |   ...          |
    +-------------------------------+----------------+
    | status / hints                                 |
    +------------------------------------------------+

Flow: write a skeleton in the editor -> [ctrl+g] Forge -> the agent searches the
library, writes documented code with a typewriter reveal -> review ->
[ctrl+p] approve (saves + indexes) / [ctrl+n] reject / [ctrl+e] edit /
[ctrl+r] regenerate a selected range.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, OptionList, Select, Static, TextArea, Tree
from textual.widgets.option_list import Option

from forger.agent import ForgeError, _used_names, forge_documented, regen_range
from forger.config import Config, canonical_language
from forger.implementer import ImplementedModule
from forger.input import InputKind, normalize
from forger.library import ext_for, write_module
from forger.llm import make_provider
from forger.manifest import Manifest
from forger.tui.vim_editor import VimTextArea

# Target language -> TextArea (tree-sitter) lexer name. Unsupported names are
# guarded at call time, so an unknown language simply disables highlighting.
_TEXTUAL_LANG = {
    "python": "python",
    "rust": "rust",
    "javascript": "javascript",
    "typescript": "typescript",
    "go": "go",
    "c": "c",
    "cpp": "cpp",
    "java": "java",
    "bash": "bash",
    "json": "json",
}


class State:
    ENTRY = "entry"
    FORGING = "forging"
    REVIEW = "review"


class InstructionScreen(ModalScreen[str]):
    """A tiny modal that collects a free-text instruction (for range regen)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self.prompt),
            Input(id="instr", placeholder="instruction (optional)"),
            Static("[enter] apply   [esc] cancel"),
            id="instr_box",
        )

    def on_mount(self) -> None:
        self.query_one("#instr", Input).focus()

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "instr":
            self.dismiss(event.value or "")

    def action_cancel(self) -> None:
        self.dismiss(None)


class LanguageScreen(ModalScreen[str]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Target language (python, rust, typescript, c, ...)"),
            Input(id="lang", placeholder="language"),
            Static("[enter] set   [esc] cancel"),
            id="lang_box",
        )

    def on_mount(self) -> None:
        self.query_one("#lang", Input).focus()

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "lang":
            self.dismiss(event.value or "")

    def action_cancel(self) -> None:
        self.dismiss(None)


class BackendScreen(ModalScreen):
    """Set the LLM backend (provider, base URL, model, key) without restarting."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, config) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        provider = self.config.provider or "anthropic"
        if provider not in ("anthropic", "openai-compat"):
            provider = "anthropic"
        yield Vertical(
            Label("LLM backend"),
            Label("Provider"),
            Select(
                [
                    ("Anthropic (Claude / GLM proxy)", "anthropic"),
                    ("OpenAI-compatible (OpenAI / vLLM / Ollama)", "openai-compat"),
                ],
                value=provider,
                id="provider",
                allow_blank=False,
            ),
            Label("Base URL"),
            Input(self.config.resolved_base_url() or "", id="base_url", placeholder="http://host:port/v1"),
            Label("Model"),
            Input(self.config.model or "", id="model", placeholder="model id"),
            Label("API key (blank = keep)"),
            Input("", id="key"),
            Static("[enter] apply   [esc] cancel"),
            id="backend_box",
        )

    def on_mount(self) -> None:
        self.query_one("#base_url", Input).focus()

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        values = {widget.id: widget.value for widget in self.query(Input)}
        values["provider"] = self.query_one("#provider", Select).value
        self.dismiss(values)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ForgeApp(App):
    CSS = """
    Screen { layout: vertical; }
    Horizontal { height: 1fr; }
    #main { width: 2fr; height: 100%; }
    #sidebar { width: 1fr; height: 100%; border-left: round $accent;
               padding: 0 0 0 1; }
    #editor { height: 1fr; border: round $primary; }
    #editor.vim-insert { border: round $success; }
    #status { height: 1; background: $boost; color: $text; padding: 0 1; }
    #funclist { height: 1fr; border: round $panel; }
    #filetree { height: 1fr; border: round $panel; }
    #sidebar > Label { background: $panel; padding: 0 1; }
    #sidebar > Input { height: 1; }
    #instr_box, #lang_box, #backend_box { background: $panel; padding: 1 2; border: round $accent; }
    #backend_box { width: 64; height: auto; }
    #backend_box Select { height: 1; }
    """

    # Textual hardcodes the command palette on ctrl+p; Func-Forger doesn't use
    # the palette, so disable it to free ctrl+p for "approve".
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("ctrl+g", "forge", "Forge"),
        Binding("ctrl+p", "approve", "Approve"),
        Binding("ctrl+n", "reject", "Reject"),
        Binding("ctrl+e", "toggle_edit", "Edit"),
        Binding("ctrl+r", "regen_range", "Regen range"),
        Binding("ctrl+k", "clear_editor", "New/Clear"),
        Binding("ctrl+b", "back", "Back"),
        Binding("ctrl+l", "focus_funcs", "Library"),
        Binding("f2", "set_language", "Language"),
        Binding("f3", "external_editor", "Vim/$EDITOR"),
        Binding("f4", "backend", "Backend"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    state = reactive(State.ENTRY)
    language = reactive("python")

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.manifest = Manifest(config.manifest_path)
        self.llm = make_provider(config)
        self.language = config.session_language or "python"
        self._last_module = None
        self._last_result = None
        self._skeleton = ""
        self._saved_entry = None  # text saved when viewing a library function
        self._vim_mode = "normal"
        self._last_status_msg = ""
        self._forging_msg = ""
        self._spin_i = 0
        self._spin_timer = None
        self._reveal_timer = None
        self._reveal_full = ""
        self._reveal_start = ""
        self._reveal_pos = 0

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="main"):
                yield VimTextArea(id="editor")
                yield Static("", id="status")
            with Vertical(id="sidebar"):
                yield Label("Library search")
                yield Input(id="search", placeholder="filter functions…")
                yield Label("Functions")
                yield OptionList(id="funclist")
                yield Label("Files")
                yield Tree("library", id="filetree")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Func-Forger"
        self.sub_title = f"provider: {self.config.provider} · model: {self.config.resolved_model()}"
        self._set_editor_language(self.language)
        self._refresh_sidebar()
        self._set_status(self._entry_hint())
        self.query_one("#editor", TextArea).focus()

    # -- helpers -----------------------------------------------------------

    def _entry_hint(self) -> str:
        return (
            f"write a skeleton (press i to type, Esc for vim cmds), "
            f"then [ctrl+g] Forge   ·   lang: {self.language}   ·   "
            f"[f2] lang  [f4] backend  [f3] $EDITOR"
        )

    def _set_status(self, msg: str) -> None:
        self._last_status_msg = msg
        self.query_one("#status", Static).update(f"[{self._vim_mode.upper()}] {msg}")

    @on(VimTextArea.ModeChanged)
    def _on_vim_mode(self, message: VimTextArea.ModeChanged) -> None:
        self._vim_mode = message.mode
        self.query_one("#status", Static).update(
            f"[{self._vim_mode.upper()}] {self._last_status_msg}"
        )

    @on(VimTextArea.VimCommand)
    def _on_vim_command(self, message: VimTextArea.VimCommand) -> None:
        if message.command == "approve":
            self.action_approve()
        elif message.command == "reject":
            self.action_reject()

    def _set_editor_language(self, lang: str) -> None:
        try:
            self.query_one("#editor", TextArea).language = _TEXTUAL_LANG.get(lang, "python")
        except Exception:
            pass

    def watch_language(self, lang: str) -> None:  # noqa: D401
        self._set_editor_language(lang)

    # -- sidebar -----------------------------------------------------------

    def _refresh_sidebar(self) -> None:
        ol = self.query_one("#funclist", OptionList)
        ol.clear_options()
        for entry in sorted(self.manifest.all(), key=lambda e: (e.target_language, e.name)):
            label = f"{entry.target_language}:{entry.name}"
            if entry.description:
                label += f" — {entry.description[:48]}"
            ol.add_option(Option(label, id=entry.id))

        tree = self.query_one("#filetree", Tree)
        tree.reset(self.config.library_dir.name or "library")
        by_lang: dict[str, set[str]] = {}
        for entry in self.manifest.all():
            by_lang.setdefault(entry.target_language, set()).add(entry.file_path)
        for lang in sorted(by_lang):
            lnode = tree.root.add(lang)
            # Build a nested tree from the slash-separated file paths so that
            # role categories (library/<lang>/<category>/<file>) appear as branches.
            nodes: dict[tuple, object] = {(): lnode}
            for path in sorted(by_lang[lang]):
                comps = path.split("/")
                cur: tuple = ()
                for i, comp in enumerate(comps):
                    key = cur + (comp,)
                    if key not in nodes:
                        parent = nodes[cur]
                        nodes[key] = (
                            parent.add_leaf(comp) if i == len(comps) - 1 else parent.add(comp)
                        )
                    cur = key
            lnode.expand_all()
        tree.root.expand_all()

    @on(Input.Changed)
    def _on_search_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search":
            return
        query = event.value.strip().lower()
        ol = self.query_one("#funclist", OptionList)
        ol.clear_options()
        for entry in sorted(self.manifest.all(), key=lambda e: (e.target_language, e.name)):
            hay = f"{entry.target_language} {entry.name} {entry.description} {entry.doc}".lower()
            if query and query not in hay:
                continue
            label = f"{entry.target_language}:{entry.name}"
            if entry.description:
                label += f" — {entry.description[:48]}"
            ol.add_option(Option(label, id=entry.id))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self.state != State.ENTRY:
            self._set_status("finish or discard the current review first")
            return
        entry = self.manifest.entries.get(event.option.id)
        if not entry:
            return
        path = self.config.library_dir / entry.file_path
        if not path.exists():
            self._set_status(f"file missing: {path}")
            return
        editor = self.query_one("#editor", TextArea)
        self._saved_entry = editor.text
        editor.read_only = False
        editor.text = path.read_text(encoding="utf-8")
        editor.read_only = True
        self._set_status(f"viewing {entry.id} — [ctrl+e] to edit, [ctrl+b] back to your skeleton")

    def action_focus_funcs(self) -> None:
        self.query_one("#funclist", OptionList).focus()

    # -- forging -----------------------------------------------------------

    def action_forge(self) -> None:
        editor = self.query_one("#editor", TextArea)
        skeleton = editor.text
        if not skeleton.strip():
            self._set_status("(editor is empty — write a skeleton first)")
            return
        self._do_forge(skeleton)

    @work(thread=True, exclusive=True)
    def _do_forge(self, skeleton: str) -> None:
        self._skeleton = skeleton
        self.call_from_thread(self._begin_forging)

        def on_turn(raw, searches, code):
            if searches:
                msg = "searching library: " + "; ".join(searches)
            elif code:
                msg = "writing documented code…"
            else:
                msg = "thinking…"
            self.call_from_thread(setattr, self, "_forging_msg", msg)

        try:
            module = normalize(skeleton, InputKind.CODE, self.language, self.llm)
            self._last_module = module
            self.call_from_thread(self._set_language, module.target_language)
            result = forge_documented(
                skeleton, module.target_language, self.manifest, self.llm, on_turn=on_turn
            )
            self.call_from_thread(self._finish_forging, result)
        except Exception as exc:
            self.call_from_thread(self._forge_failed, str(exc))

    def _set_language(self, lang: str) -> None:
        self.language = lang

    def _begin_forging(self) -> None:
        self.state = State.FORGING
        self._forging_msg = "starting…"
        self._spin_i = 0
        self.query_one("#editor", TextArea).read_only = True
        self._spin_timer = self.set_interval(0.12, self._spin_tick)

    def _spin_tick(self) -> None:
        if self.state != State.FORGING:
            return
        glyphs = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._spin_i = (self._spin_i + 1) % len(glyphs)
        self.query_one("#status", Static).update(f"{glyphs[self._spin_i]} {self._forging_msg}")

    def _finish_forging(self, result) -> None:
        self._stop_spinner()
        self._last_result = result
        self._reveal_code(result.code)

    def _reveal_code(self, code: str) -> None:
        self._reveal_full = code
        # Morph from the current text (the skeleton) into the generated code,
        # so it visibly transforms top-to-bottom rather than appearing from blank.
        self._reveal_start = self.query_one("#editor", TextArea).text
        self._reveal_pos = 0
        self._reveal_timer = self.set_interval(0.02, self._reveal_tick)

    def _reveal_tick(self) -> None:
        self._reveal_pos = min(self._reveal_pos + 28, len(self._reveal_full))
        new, skeleton, pos = self._reveal_full, self._reveal_start, self._reveal_pos
        shown = new[:pos] + skeleton[pos:] if pos < len(skeleton) else new[:pos]
        self.query_one("#editor", TextArea).text = shown
        if self._reveal_pos >= len(self._reveal_full):
            self._reveal_timer.stop()
            self._enter_review()

    def _enter_review(self) -> None:
        editor = self.query_one("#editor", TextArea)
        editor.read_only = True
        self.state = State.REVIEW
        known, unknown = _used_names(editor.text, self.manifest, self.language)
        parts = ["review"]
        if known:
            parts.append(f"reuses {', '.join(known)}")
        if unknown:
            parts.append(f"⚠ unknown calls: {', '.join(unknown)}")
        self._set_status(
            "  ·  ".join(parts)
            + "  —  [ctrl+p] approve  [ctrl+n] reject  [ctrl+e] edit  [ctrl+r] regen-range"
        )

    def _forge_failed(self, msg: str) -> None:
        self._stop_spinner()
        self.state = State.ENTRY
        self.query_one("#editor", TextArea).read_only = False
        self._set_status(f"✗ forge failed: {msg}")

    def _stop_spinner(self) -> None:
        if self._spin_timer is not None:
            self._spin_timer.stop()
            self._spin_timer = None

    # -- review actions ----------------------------------------------------

    def action_approve(self) -> None:
        if self.state != State.REVIEW or self._last_module is None:
            return
        editor = self.query_one("#editor", TextArea)
        code = editor.text  # may be hand-edited
        language = self._last_module.target_language
        known, _ = _used_names(code, self.manifest, language)
        implemented = ImplementedModule(code=code, used_names=known)
        result = write_module(self._last_module, implemented, self.config.library_dir, self.manifest)
        for entry in result.entries:
            self.manifest.upsert(entry)
        self.manifest.persist()
        self._refresh_sidebar()
        editor.text = ""
        editor.read_only = False
        self._last_module = None
        self._last_result = None
        self._skeleton = ""
        self.state = State.ENTRY
        warn = f"  ⚠ verify: {', '.join(result.unknown_deps)}" if result.unknown_deps else ""
        self._set_status(
            f"saved {result.rel_path} ({len(result.entries)} fn){warn}  ·  "
            + self._entry_hint()
        )

    def action_reject(self) -> None:
        if self.state != State.REVIEW:
            return
        editor = self.query_one("#editor", TextArea)
        editor.read_only = False
        editor.text = self._skeleton
        self._last_module = None
        self._last_result = None
        self.state = State.ENTRY
        self._set_status("rejected — skeleton restored. Edit and [ctrl+g] Forge again.")

    def action_toggle_edit(self) -> None:
        editor = self.query_one("#editor", TextArea)
        if self.state == State.REVIEW:
            editor.read_only = not editor.read_only
            editor.focus()
            if editor.read_only:
                self._enter_review()
            else:
                self._set_status("editing — [ctrl+p] approve when done, [ctrl+e] to lock again")
        elif self.state == State.ENTRY and self._saved_entry is not None:
            editor.read_only = False
            editor.focus()
            self._set_status("editing — [ctrl+b] back to your skeleton")

    def action_regen_range(self) -> None:
        if self.state != State.REVIEW:
            return
        editor = self.query_one("#editor", TextArea)
        region = editor.selected_text
        if not region.strip():
            self._set_status("select a range first, then [ctrl+r]")
            return
        self.push_screen(
            InstructionScreen("Describe the change for the selected range (optional):"),
            self._on_regen_instruction,
        )

    def _on_regen_instruction(self, instruction) -> None:
        if instruction is None:
            return
        editor = self.query_one("#editor", TextArea)
        full = editor.text
        region = editor.selected_text or full
        self._begin_forging()
        self._forging_msg = "regenerating range…"
        self._run_regen(full, region, instruction)

    @work(thread=True, exclusive=True)
    def _run_regen(self, full: str, region: str, instruction: str) -> None:
        try:
            new_code = regen_range(full, region, instruction, self.language, self.llm)
            self.call_from_thread(self._apply_regen, new_code)
        except Exception as exc:
            self.call_from_thread(self._forge_failed, str(exc))

    def _apply_regen(self, new_code: str) -> None:
        self._stop_spinner()
        if self._last_result is not None:
            self._last_result.code = new_code
        self._reveal_code(new_code)

    # -- misc actions ------------------------------------------------------

    def action_clear_editor(self) -> None:
        if self.state == State.FORGING:
            return
        editor = self.query_one("#editor", TextArea)
        editor.read_only = False
        editor.text = ""
        self._saved_entry = None
        self._last_module = None
        self._last_result = None
        self.state = State.ENTRY
        self._set_status(self._entry_hint())
        editor.focus()

    def action_back(self) -> None:
        if self._saved_entry is None:
            return
        editor = self.query_one("#editor", TextArea)
        editor.read_only = False
        editor.text = self._saved_entry
        self._saved_entry = None
        self.state = State.ENTRY
        self._set_status("restored your skeleton — [ctrl+g] Forge")
        editor.focus()

    def action_set_language(self) -> None:
        self.push_screen(LanguageScreen(), self._on_language)

    def _on_language(self, value) -> None:
        if not value:
            return
        lang = canonical_language(value)
        if lang:
            self.language = lang
            self._set_status(f"language = {lang}  ·  " + self._entry_hint())

    def _rebuild_llm(self) -> None:
        try:
            self.llm = make_provider(self.config)
        except Exception as exc:  # e.g. unknown provider string
            self._set_status(f"provider rebuild failed: {exc}")

    def action_backend(self) -> None:
        self.push_screen(BackendScreen(self.config), self._on_backend)

    def _on_backend(self, values) -> None:
        if not values:
            return
        provider = (values.get("provider") or "").strip()
        base_url = (values.get("base_url") or "").strip()
        model = (values.get("model") or "").strip()
        key = values.get("key") or ""
        if provider:
            self.config.provider = provider
        if base_url:
            self.config.base_url = base_url
        if model:
            self.config.model = model
        if key:
            self.config.api_key = key
        self._rebuild_llm()
        self.sub_title = (
            f"provider: {self.config.provider} · model: {self.config.resolved_model()}"
        )
        self._set_status(
            f"backend = {self.config.provider} / {self.config.resolved_model()} "
            f"/ {self.config.resolved_base_url() or '(default)'}"
        )

    def action_external_editor(self) -> None:
        """Open the current buffer in ``$EDITOR`` (vim by default) and load it back.

        Gives a true vim/emacs/etc. editing session on the skeleton or the
        generated code under review. The app is suspended while the external
        editor owns the terminal.
        """
        if self.state == State.FORGING:
            return
        editor_widget = self.query_one("#editor", TextArea)
        fd, tmp_path = tempfile.mkstemp(suffix=ext_for(self.language), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(editor_widget.text)
            command = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
            try:
                with self.suspend():
                    subprocess.call([command, tmp_path])
            except Exception as exc:  # SuspendNotSupported, or the editor failed to launch
                self._set_status(f"external editor unavailable: {exc}")
                return
            try:
                editor_widget.text = Path(tmp_path).read_text(encoding="utf-8")
            except OSError:
                return
            editor_widget.read_only = False
            editor_widget.focus()
            action = "[ctrl+p] approve" if self.state == State.REVIEW else "[ctrl+g] forge"
            self._set_status(f"edited in $EDITOR — {action}")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
