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
model (`BAAI/bge-small-en-v1.5`) for matches token search misses. On first use
it auto-installs `fastembed` (ONNX-based, no torch) and downloads the model —
nothing for you to configure.

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
pip install -e .                 # installs anthropic, openai, textual
forger                           # work in ./library (default)
forger --library ./my_project    # work on an existing project (re-indexed on every startup)
forger --provider openai-compat --base-url http://localhost:11434/v1 --model llama3.1
```

Bring your own model. Func-Forger talks to any LLM API you configure:

- **Proprietary** — Anthropic Claude, OpenAI GPT, Google Gemini, …
- **Open-source / self-hosted** — anything behind an OpenAI-compatible endpoint
  (Ollama, vLLM, LM Studio, Together, Groq, …) or an Anthropic-compatible proxy.

Provider, base URL, model, and API key are set via CLI flags, environment
variables, or the in-app backend screen (`F4`). Credentials are read from the
usual environment variables (`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` /
`OPENAI_API_KEY`, optional `*_BASE_URL`).

**Two things to know about working on an existing project:**

- Func-Forger **modifies the directory** it works in — it writes `manifest.json`
  and adds newly forged files. **Back up your original first.**
- It **re-indexes the directory on every startup** (full rebuild), so the index
  always reflects your current source. Indexing is *approximate* (the LLM may
  miss or mis-describe some definitions) and costs one LLM call per source file,
  so a large project takes time on each launch. Non-text (binary) files are
  skipped automatically, as are noisy directories (`.git`, `node_modules`,
  `venv`, `build`, …).

## Status

Early / in active design.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This project was developed using Claude Code powered by GLM-5.2 and has not been
thoroughly reviewed by a human developer. You are free to use it, but the author
assumes no responsibility for any problems that may arise from its use.
