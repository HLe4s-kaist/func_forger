# Func-Forger

> Languages: **English** · [한국어](README.ko.md) · [中文](README.zh.md) · [日本語](README.ja.md) — English is the canonical version.

> **The idea.** Func-Forger goes beyond handing everything to an LLM agent. It
> uses the LLM to accelerate the whole coding workflow while *reducing* the
> inferential burden placed on the model — so the human keeps the initiative
> over the entire codebase, and cheaper open-source or lower-tier models can
> drive the project effectively. It shines most where **only local models are
> available**.

Most AI coding tools make the LLM responsible for *what* to build, *how* to
structure it, and *how* to implement it — a heavy load that only the strongest
(and priciest) models carry well, and that quietly hands the shape of the
codebase to the model. Func-Forger flips the division of labor:

- **The human holds the design.** You draft *skeletons* — the bones of every
  top-level definition (functions, structs/classes, typedefs, macros, …) plus
  optional direction comments. *What* gets built and *how* the codebase is
  organized stay your decisions.
- **The LLM only fills in well-specified bodies** — implementation, not
  architecture. That is a much smaller task, so a weaker or cheaper model is
  enough, and the output stays simple for a human to validate.
- **Built work is reused, bottom-up.** Each implemented definition is documented
  (rustdoc-grade), indexed, and filed into a growing library; new definitions
  search that library and compose existing ones instead of being reinvented.

You stay in command of the codebase while the LLM accelerates the grind — and a
local open-source model is enough; no cloud model required.

**One directory is the whole library.** Point Func-Forger at a directory
(`--library <dir>`, default `./library`). On every startup it re-indexes the
source already there (so the index always reflects your code); everything you
forge is added to that same directory and becomes reusable too.

## The interface (TUI)

A two-pane terminal app:

```
+-------------------------------+----------------+
|                               | search [____]  |
|         editor                | functions      |
|   skeleton  <-->  code        |   ...          |
|                               | files          |
|                               |   ...          |
+-------------------------------+----------------+
| status / hints                                  |
+------------------------------------------------+
```

- **Left** — a code editor for skeletons (and for reviewing/editing generated
  code). The editor is vim-modal in place (`i` to type, `Esc` for commands);
  press `F3` to open the buffer in `$EDITOR` (vim) for a full modal session.
- **Right sidebar** — live library **search**, the **definition list** (select
  an entry to view its source), and a **file tree** of the directory grouped by
  language and role.

### Keybindings

| Key | Action |
|---|---|
| `Ctrl+G` | **Forge** — implement the skeleton in the editor |
| `Ctrl+P` | **Approve** the generated code (save + index + refresh sidebar) |
| `Ctrl+N` | **Reject** (restore the skeleton) |
| `Ctrl+E` | Toggle **edit** mode on the generated code |
| `Ctrl+R` | **Regenerate** only the selected range (range-locked; instruction optional) |
| `Ctrl+K` | Clear the editor for a new skeleton |
| `Ctrl+B` | Back to your in-progress skeleton |
| `Ctrl+L` | Focus the definition list |
| `F2` | Set the target language |
| `F3` | Open the buffer in `$EDITOR` (vim) |
| `F4` | Open the **LLM backend** settings (provider / base URL / model / key) |
| `Ctrl+Q` | Quit |

### The forge flow

1. Write a skeleton in the editor, then `Ctrl+G`.
2. The **agent** searches the library (a `SEARCH:`/`LOOKUP:` tool **plus**
   auto-seeded candidates) and composes existing definitions into the new one
   wherever it can.
3. It writes **rustdoc-style docs** above each definition (summary, Arguments,
   Returns, Behavior). That documentation is indexed and drives future search.
4. A spinner runs during generation, then the code is revealed with a
   skeleton→code animation.
5. **Review** — approve, reject, hand-edit, or regenerate a selected range.
   Approve writes the file into the directory and refreshes the sidebar.

## Search & reuse is the point

Every definition's documentation is extracted into a `manifest.json` in the
directory and searched over. The default search is dependency-free token
overlap: identifiers are split (`double_sum`, `parseCSV` → words), generic
stopwords are dropped, and the score weights the **name** above the
**description** above the **doc**. So a query like `sum two integers` finds
`add` even with no shared surface form — which is what lets `double_sum` reuse
`add` automatically.

**Semantic search is on by default.** Func-Forger uses a small local embedding
model (`BAAI/bge-small-en-v1.5`) for matches token search misses. `fastembed`
(ONNX-based, no torch) is installed by `pip install -e .`; the model downloads
on first search. Use `--no-embed` for token search only.

Use a different model, or turn it off:

```bash
forger --embed-model BAAI/bge-base-en-v1.5     # any fastembed-supported model
forger --embed-provider sentence-transformers \
       --embed-model sentence-transformers/all-MiniLM-L6-v2   # or use sentence-transformers
forger --no-embed                              # token search only (no extra deps)
```

## Language-agnostic, and what it does *not* do

Generated code can be in **any language**. Because Func-Forger never executes
the code, it has no per-language runtime dependency — which is what makes true
language-independence possible.

Func-Forger **generates and stores code only**. It does not compile, run, or
test anything. If the skeletons are well-formed, validating the output is
straightforward — and that validation is left to the human, by design.

## LLM backend & running

```bash
pip install -e .                 # installs anthropic, openai, textual, fastembed
forger                           # work in ./library (default); embeddings on by default
forger --library ./my_project    # work on an existing project (indexed once on first run)
forger --provider openai-compat --base-url http://localhost:11434/v1 --model llama3.1
```

Bring your own model. Func-Forger talks to any LLM API you configure:

- **Proprietary** — Anthropic Claude, OpenAI GPT, Google Gemini, …
- **Open-source / self-hosted** — anything behind an OpenAI-compatible endpoint
  (Ollama, vLLM, LM Studio, Together, Groq, …) or an Anthropic-compatible proxy.

### Command-line options

| Flag | Env var | Description |
|---|---|---|
| `--library`, `-l` | `FORGER_LIBRARY` | library/codebase directory (default: `./library`) |
| `--provider` | `FORGER_PROVIDER` | LLM backend: `anthropic` \| `openai-compat` (default: `anthropic`) |
| `--model` | `FORGER_MODEL` | model id (e.g. `glm-4.6`, `claude-sonnet-4-6`, `gpt-4o-mini`) |
| `--base-url` | `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` | API base URL |
| `--lang` | `FORGER_LANG` | default target language |
| `--embed-provider` | `FORGER_EMBED_PROVIDER` | semantic search: `fastembed` \| `sentence-transformers` \| `none` (default: `fastembed`) |
| `--embed-model` | `FORGER_EMBED_MODEL` | embedding model id or local path (default: `BAAI/bge-small-en-v1.5`) |
| `--embed` | — | enable semantic search (same as the default) |
| `--no-embed` | — | disable semantic search (token search only) |
| `--repl` | — | use the legacy conversational REPL instead of the TUI |

API keys are read from `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` or
`OPENAI_API_KEY`. Backend settings can also be changed live in the app with `F4`.

**Working on an existing project:**

- Func-Forger **modifies the directory** it works in — it writes `manifest.json`
  and adds newly forged files. **Back up your original first.**
- It **indexes the directory once, on first run** (when no `manifest.json` exists
  yet). After that the manifest persists; to re-index after big edits, delete
  `manifest.json` and relaunch. Indexing is *approximate* (the LLM may miss or
  mis-describe some definitions) and costs one LLM call per source file. Source
  is read as UTF-8; other text encodings (UTF-16, CJK legacy) are detected
  best-effort. Binary files and noisy directories (`.git`, `node_modules`, `venv`,
  `build`, …) are skipped automatically.

## Status

Early / in active design.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This project was developed using Claude Code powered by GLM-5.2 and has not been
thoroughly reviewed by a human developer. You are free to use it, but the author
assumes no responsibility for any problems that may arise from its use.
