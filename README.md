# Func-Forger

AI coding agents write code at a volume humans can't keep up with — and
prompt-engineering or harness tricks can't validate the code itself; they only
steer the generation. Func-Forger takes the opposite stance: **give the
initiative back to the programmer, and use the LLM only as an accelerator**,
following a human's bottom-up development intuition.

The human plays the architect: drafting only the *skeletons* of the functions
they want (the bones + optional `//` direction comments). The LLM implements
them, writes rustdoc-grade documentation, files them into a structured library,
and — crucially — **reuses the functions already in that library when it builds
new ones**. Small primitives are composed into larger ones, bottom-up.

> Philosophy: **the human decides what functions to build, bottom-up; the LLM
> implements them, documents them, and structures the codebase.**

## Works well even with open-source models

This workflow offloads most of the inferential burden from the LLM: the human
supplies the skeleton and the design, and delegates only the implementation.
Because the model never has to decide *what* to build or *how* to structure it
-- only how to fill in well-specified bodies -- Func-Forger stays genuinely
useful even when all you have are open-source / self-hosted models.

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

