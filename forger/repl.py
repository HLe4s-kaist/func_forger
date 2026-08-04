"""The conversational REPL: command dispatch and the forge pipeline.

The forge pipeline ties the pieces together:

    input -> classify -> normalize -> retrieve -> implement -> parse
           -> write_module -> manifest.upsert/persist -> summary

Bare input (no command word) is treated as ``forge <input>`` for fast
iteration. Plain :func:`input` reads one line, so multi-line skeletons are best
supplied as a file path (``forge ./skel.py``); a richer multiline editor is a
later enhancement.
"""

from __future__ import annotations

from pathlib import Path

from forger.config import Config, canonical_language
from forger.implementer import implement, parse
from forger.input import LanguageRequired, classify, normalize
from forger.library import write_module
from forger.llm import make_provider
from forger.manifest import Manifest, ManifestEntry
from forger.retrieve import retrieve

HELP_TEXT = """\
Commands:
  <skeleton>          forge from pasted code, a file path, or natural language
  forge <input>       same as above, explicit
  list [language]     list implemented functions
  show <name>         print the source file defining <name>
  tree                library grouped by language, with dependencies
  :lang <language>    set the target language (py, ts, c, rs, go, ...)
  :lib <path>         switch library directory (reloads the manifest)
  :model <id>         switch the LLM model
  :provider <name>    switch backend: anthropic | openai-compat
  :base_url <url>     set the API base URL (e.g. http://localhost:11434/v1)
  :key <key>          set the API key
  help                show this help
  quit                exit

Tip: multi-line skeletons are best given as a file path, e.g.  forge ./skel.py
"""


class REPL:
    def __init__(self, config: Config):
        self.config = config
        self.manifest = Manifest(config.manifest_path)
        self.llm = make_provider(config)
        self.running = True

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> None:
        print("Func-Forger 0.1.0  —  type a skeleton to forge, or `help`.")
        print(
            f"library: {self.config.library_dir}   "
            f"provider: {self.config.provider}   model: {self.config.resolved_model()}"
        )
        while self.running:
            try:
                line = input("forger> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if line:
                self.dispatch(line)

    def reload_manifest(self) -> None:
        self.manifest = Manifest(self.config.manifest_path)

    def _rebuild_llm(self) -> None:
        try:
            self.llm = make_provider(self.config)
        except Exception as exc:  # provider config error
            self._error(f"provider rebuild failed: {exc}")

    # -- dispatch ----------------------------------------------------------

    def dispatch(self, line: str) -> None:
        low = line.lower()
        if low in ("quit", "exit", ":q"):
            self.running = False
            return
        if low in ("help", ":h", "?"):
            print(HELP_TEXT)
            return
        if low == "tree":
            self.cmd_tree()
            return
        if low == "list" or low.startswith("list "):
            self.cmd_list(line[len("list"):].strip())
            return
        if low.startswith("show "):
            self.cmd_show(line[len("show"):].strip())
            return
        if line.startswith(":"):
            self.cmd_colon(line[1:])
            return
        if low.startswith("forge "):
            self.cmd_forge(line[len("forge"):].strip())
            return
        # Bare input is shorthand for `forge <input>`.
        self.cmd_forge(line)

    # -- commands ----------------------------------------------------------

    def cmd_forge(self, text: str) -> None:
        if not text:
            self._note("Nothing to forge.")
            return

        kind = classify(text)
        try:
            module = normalize(text, kind, self.config.session_language, self.llm)
        except LanguageRequired:
            lang = input("  target language (python, typescript, c, ...): ").strip()
            if not lang:
                self._note("Skipped. Set a language with `:lang <language>`.")
                return
            self.config.session_language = canonical_language(lang)
            try:
                module = normalize(text, kind, self.config.session_language, self.llm)
            except Exception as exc:
                self._error(f"normalization failed: {exc}")
                return
        except Exception as exc:
            self._error(f"normalization failed: {exc}")
            return

        language = module.target_language
        if not self.config.session_language:
            self.config.session_language = language
        self._note(
            f"input={kind.value}  language={language}  "
            f"functions={module.names()}"
        )

        retrieved = self._retrieve_union(module)
        if retrieved:
            self._note("reusing: " + ", ".join(e.name for e in retrieved))

        known_names = {
            e.name for e in self.manifest.all() if e.target_language == language
        }

        try:
            raw = implement(module, retrieved, self.llm)
            implemented = parse(raw, module, known_names)
        except Exception as exc:
            self._error(f"implementation failed: {exc}")
            return

        collisions = [
            fn.name for fn in module.definitions if self.manifest.get(language, fn.name)
        ]
        if collisions:
            self._note("overwriting existing: " + ", ".join(collisions))

        result = write_module(module, implemented, self.config.library_dir, self.manifest)
        for entry in result.entries:
            self.manifest.upsert(entry)
        self.manifest.persist()

        self._note(
            f"wrote {result.rel_path}  ({len(result.entries)} function(s))"
        )
        if implemented.used_names:
            self._note("calls library: " + ", ".join(implemented.used_names))
        if result.unknown_deps:
            self._warn(
                "references functions not in library: "
                + ", ".join(result.unknown_deps)
                + " (verify by hand)"
            )

    def _retrieve_union(self, module) -> list[ManifestEntry]:
        retrieved: list[ManifestEntry] = []
        seen: set[str] = set()
        for fn in module.definitions:
            for entry in retrieve(fn, self.manifest):
                if entry.id not in seen:
                    seen.add(entry.id)
                    retrieved.append(entry)
        return retrieved

    def cmd_list(self, language: str) -> None:
        language = language or None
        entries = [
            e for e in self.manifest.all() if not language or e.target_language == language
        ]
        if not entries:
            self._note("(library is empty)")
            return
        for e in entries:
            print(f"  {e.target_language}:{e.name}  {e.signature}  [{e.file_path}]")

    def cmd_show(self, name: str) -> None:
        entry = self._find(name)
        if not entry:
            self._error(f"No function named {name!r}. Try `list`.")
            return
        abs_path = self.config.library_dir / entry.file_path
        if not abs_path.exists():
            self._error(f"File missing on disk: {abs_path}")
            return
        print(abs_path.read_text(encoding="utf-8"))

    def cmd_tree(self) -> None:
        by_lang: dict[str, list[ManifestEntry]] = {}
        for e in self.manifest.all():
            by_lang.setdefault(e.target_language, []).append(e)
        if not by_lang:
            self._note("(library is empty)")
            return
        for lang in sorted(by_lang):
            print(f"{lang}/")
            for e in sorted(by_lang[lang], key=lambda x: x.name):
                deps = ", ".join(d.split(":", 1)[1] for d in e.depends_on)
                tail = f"  -> {deps}" if deps else ""
                print(f"  {e.name}  [{e.file_path}]{tail}")

    def cmd_colon(self, line: str) -> None:
        parts = line.split(None, 1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "lang":
            if not arg:
                print(f"session language: {self.config.session_language or '(none)'}")
                return
            self.config.session_language = canonical_language(arg)
            self._note(f"session language = {self.config.session_language}")
        elif cmd == "lib":
            if not arg:
                print(f"library: {self.config.library_dir}")
                return
            self.config.library_dir = Path(arg)
            self.reload_manifest()
            self._note(f"library = {self.config.library_dir}")
        elif cmd == "model":
            if not arg:
                print(f"model: {self.config.resolved_model()}")
                return
            self.config.model = arg
            self._rebuild_llm()
            self._note(f"model = {arg}")
        elif cmd in ("provider", "backend"):
            if not arg:
                print(f"provider: {self.config.provider}")
                return
            self.config.provider = arg
            self._rebuild_llm()
            self._note(f"provider = {arg}")
        elif cmd in ("base_url", "baseurl"):
            if not arg:
                print(f"base_url: {self.config.resolved_base_url() or '(default)'}")
                return
            self.config.base_url = arg
            self._rebuild_llm()
            self._note(f"base_url = {arg}")
        elif cmd == "key":
            if not arg:
                print("api key: <set>" if self.config.resolved_api_key() else "api key: (none)")
                return
            self.config.api_key = arg
            self._rebuild_llm()
            self._note("api key set")
        else:
            self._error(f"unknown command :{cmd}. Try `help`.")

    # -- helpers -----------------------------------------------------------

    def _find(self, name: str) -> ManifestEntry | None:
        if self.config.session_language:
            entry = self.manifest.get(self.config.session_language, name)
            if entry:
                return entry
        for e in self.manifest.all():
            if e.name == name:
                return e
        return None

    def _note(self, msg: str) -> None:
        print(f"  · {msg}")

    def _warn(self, msg: str) -> None:
        print(f"  ! {msg}")

    def _error(self, msg: str) -> None:
        print(f"  ✗ {msg}")
