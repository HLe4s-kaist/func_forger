# Func-Forger

> Languages: **English** · [한국어](README.ko.md) · [中文](README.zh.md) · [日本語](README.ja.md) — English is the canonical version.

> **The idea.** Func-Forger goes beyond handing everything to an LLM agent. It
> uses the LLM to accelerate the whole coding workflow while *reducing* the
> inferential burden placed on the model — so the human keeps the initiative
> over the entire codebase, and cheaper open-source or lower-tier models can
> drive the project effectively. It shines most in environments where **only
> local models are available**.

Most AI coding tools make the LLM responsible for *what* to build, *how* to
structure it, and *how* to implement it — a heavy inferential load that only
the strongest (and priciest) models carry well, and that quietly hands the
shape of the codebase over to the model. Func-Forger flips the division of
labor:

- **The human holds the design.** You draft *skeletons* — the bones of every
  top-level definition (functions, structs/classes, typedefs, macros, …) plus
  optional direction comments — so *what* gets built and *how* the codebase is
  organized stay your decisions, not the model's.
- **The LLM only fills in well-specified bodies.** Implementation, not
  architecture. That is a far smaller inferential task, so a weaker or cheaper
  model is enough — and the output stays simple for a human to validate.
- **Built work is reused, bottom-up.** Each implemented definition is documented
  (rustdoc-grade), indexed, and filed into a growing library; new definitions
  search that library and compose existing ones instead of being reinvented.

The net effect: you stay in command of the codebase while the LLM accelerates
the grind — and a local open-source model is enough; no cloud model required.

> Philosophy: **the human designs bottom-up; the LLM implements, documents, and
> structures — and because the LLM only implements, even modest models suffice.**


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

- **Left**: a code editor for skeletons (and for reviewing/editing generated code).
  Press `F3` to open the current buffer in `$EDITOR` (vim) for a real modal session.
- **Right sidebar**: live library **search**, the **function list** (select an
  entry to view its source), and a **file tree** grouped by language and role.

### Keybindings

| Key | Action |
|---|---|
| `Ctrl+G` | **Forge** — implement the skeleton in the editor |
| `Ctrl+P` | **Approve** the generated code (save + index + refresh sidebar) |
| `Ctrl+N` | **Reject** (restore the skeleton) |
| `Ctrl+E` | Toggle **edit** mode on the generated code |
| `Ctrl+R` | **Regenerate** only the selected range (range-locked; instruction optional) |
| `Ctrl+K` | Clear the editor for a new skeleton |
| `Ctrl+B` | Back to your in-progress skeleton (after viewing a library function) |
| `Ctrl+L` | Focus the function list |
| `F2` | Set the target language |
| `F3` | Open the buffer in `$EDITOR` (vim proxy) |
| `F4` | Open the **LLM backend** settings (provider / base URL / model / key) |
| `Ctrl+Q` | Quit |

### The forge flow

1. Write a skeleton in the editor, then `Ctrl+G`.
2. The **agent** searches the library (an explicit `SEARCH:` tool **plus**
   auto-seeded candidates) and composes existing functions into the new one
   wherever it can.
3. It writes **rustdoc-style docs** above each function (summary, Arguments,
   Returns, Behavior). That documentation is indexed and drives future search.
4. A spinner runs during generation, then the code is revealed with a
   skeleton→code animation.
5. **Review**: approve, reject, hand-edit, or regenerate a selected range.
   Approve writes the file into `library/<lang>/<category>/<module>.<ext>`,
   indexes it, and refreshes the sidebar.

## Search & reuse is the point

Every function's documentation is extracted into the manifest and searched over.
Tokenization splits identifiers (`double_sum`, `parseCSV` → words) and drops
generic stopwords, and scoring weights the **name** above the **description**
above the **doc**, so a query like `sum two integers` finds `add` even with no
shared surface form. This is what lets `double_sum` reuse `add` automatically.

## Language-agnostic

Generated code can be in **any language**. Because Func-Forger never executes
the code, it has no per-language runtime dependency — which is what makes true
language-independence possible.

## What it does *not* do

Func-Forger **generates and stores code only**. It does not compile, run, or
test anything. If the skeletons are well-formed, validating the output is
straightforward — and that validation is left to the human, by design.

## Configurable LLM backend

Bring your own model. Func-Forger talks to any LLM API you configure:

- **Proprietary** — Anthropic Claude, OpenAI GPT, Google Gemini, …
- **Open-source / self-hosted** — anything behind an OpenAI-compatible endpoint
  (Ollama, vLLM, LM Studio, Together, Groq, …) or an Anthropic-compatible proxy.

Provider, base URL, model, and API key are configurable via CLI flags,
environment variables, or in-app commands.

## Running

```bash
pip install -e .            # installs anthropic, openai, textual
forger                      # TUI (default)
forger --repl               # legacy conversational REPL
forger --provider openai-compat --base-url http://localhost:11434/v1 --model llama3.1
```

Credentials are read from the usual environment variables (`ANTHROPIC_API_KEY` /
`ANTHROPIC_AUTH_TOKEN` / `OPENAI_API_KEY`, optional `*_BASE_URL`).

## Status

Early / in active design.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This project was developed using Claude Code powered by GLM-5.2 and has not been
thoroughly reviewed by a human developer. You are free to use it, but the author
assumes no responsibility for any problems that may arise from its use.

