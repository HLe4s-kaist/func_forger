# Func-Forger

AI coding agents (Claude Code, Codex CLI, …) write code at a volume humans can't
keep up with — and prompt-engineering or harness tricks can't validate the code
itself; they only steer the generation. Func-Forger takes the opposite stance:
**give the initiative back to the programmer, and use the LLM only as an
accelerator**, following a human's bottom-up development intuition.

The human plays the architect — drafting only the *skeletons* of the functions
they want, the way a CS professor hands out assignment starter code (function
signatures, argument and return types, an optional description; bodies left
empty). The LLM then implements those bodies, files them away in a growing,
well-structured library, and — crucially — **reuses the functions already in
that library when it builds new ones**. Small primitives are composed into
larger ones, bottom-up.

> Philosophy: **the human decides what functions to build, bottom-up; the LLM
> implements them and structures the codebase.**

## The core loop

1. **Human drafts skeletons** — at the compilation-unit (module) level, only the
   frame: signatures, types, an optional description.
2. **Agent implements** the function bodies.
3. **Agent stores** each implemented function as a real file in a structured
   library on disk, and indexes it (signature + one-line description + path).
4. **Next skeleton → agent forges**: it searches its own library for relevant
   existing functions and reuses (calls) them instead of reimplementing.

All of this is driven **conversationally**, in a chat-style REPL.

## Inputs

Func-Forger auto-detects how you hand it a skeleton:

- **Pasted code** — drop in real skeleton code (`def add(a: int, b: int) -> int: ...`).
- **A file path** — point it at a file full of skeletons.
- **Natural language** — describe the function in words; the agent designs the
  signature and then implements it.

## Language-agnostic

Generated code can be in **any language**. Because Func-Forger never executes
the code, it has no per-language runtime dependency — which is exactly what
makes true language-independence possible.

## What it does *not* do

Func-Forger **generates and stores code only**. It does not compile, run, or test
anything. If the skeletons are well-formed, validating the output is
straightforward — and that validation is left to the human, by design.

## Configurable LLM backend

Bring your own model. Func-Forger talks to any LLM API you configure:

- **Proprietary** — Anthropic Claude, OpenAI GPT, Google Gemini, …
- **Open-source / self-hosted** — anything behind an OpenAI-compatible endpoint
  (Ollama, vLLM, LM Studio, Together, Groq, …).

Provider, base URL, model, and API key are all configurable.

## Status

Early / in active design.
