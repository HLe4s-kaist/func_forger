"""LLM provider abstraction with streaming support.

Two providers share one interface so the rest of the code is provider-agnostic:

* :class:`AnthropicProvider` -- Claude (or any Anthropic-compatible proxy).
* :class:`OpenAIProvider` -- any OpenAI-compatible Chat Completions endpoint
  (OpenAI, Ollama, vLLM, LM Studio, Together, Groq, ...).

Both support ``on_chunk`` for real-time streaming of the response text.
"""

from __future__ import annotations

from typing import Protocol


class LLMProvider(Protocol):
    """A minimal chat-completion interface used by the implementer."""

    def complete(
        self,
        system: str,
        messages: list[dict],
        *,
        model: str | None = None,
        on_chunk=None,
    ) -> str:
        ...


class AnthropicProvider:
    """Claude via the ``anthropic`` SDK (or any Anthropic-compatible proxy)."""

    def __init__(self, api_key: str | None, base_url: str | None = None, model: str | None = None):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._client = None

    def _ensure(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise RuntimeError(
                    "The `anthropic` SDK is not installed. Run `pip install anthropic`."
                ) from e
            kwargs = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = anthropic.Anthropic(**kwargs)

    def complete(self, system: str, messages: list[dict], *, model: str | None = None, on_chunk=None) -> str:
        self._ensure()
        model = model or self._model
        if on_chunk:
            try:
                parts: list[str] = []
                with self._client.messages.stream(
                    model=model, max_tokens=4096, system=system, messages=messages
                ) as stream:
                    for text in stream.text_stream:
                        on_chunk(text)
                        parts.append(text)
                return "".join(parts)
            except Exception:
                pass  # streaming not supported; fall back below
        for attempt in range(2):
            try:
                resp = self._client.messages.create(
                    model=model, max_tokens=4096, system=system, messages=messages
                )
                return "".join(block.text for block in resp.content if getattr(block, "text", None))
            except Exception:
                if attempt == 0:
                    continue  # retry once on connection error
                raise


class OpenAIProvider:
    """Any OpenAI-compatible Chat Completions endpoint."""

    def __init__(self, api_key: str | None, base_url: str | None = None, model: str | None = None):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._client = None

    def _ensure(self):
        if self._client is None:
            try:
                import openai
            except ImportError as e:
                raise RuntimeError(
                    "The `openai` SDK is not installed. Run `pip install openai`."
                ) from e
            kwargs = {"api_key": self._api_key or "not-required"}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.OpenAI(**kwargs)

    def complete(self, system: str, messages: list[dict], *, model: str | None = None, on_chunk=None) -> str:
        self._ensure()
        model = model or self._model
        msgs = [{"role": "system", "content": system}, *messages]
        if on_chunk:
            try:
                parts: list[str] = []
                stream = self._client.chat.completions.create(
                    model=model, messages=msgs, max_tokens=4096, stream=True
                )
                for chunk in stream:
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        on_chunk(delta)
                        parts.append(delta)
                return "".join(parts)
            except Exception:
                pass
        for attempt in range(2):
            try:
                resp = self._client.chat.completions.create(model=model, messages=msgs, max_tokens=4096)
                return resp.choices[0].message.content or ""
            except Exception:
                if attempt == 0:
                    continue
                raise


def make_provider(config) -> LLMProvider:
    """Build the provider selected by ``config.provider``."""
    provider = (config.provider or "anthropic").strip().lower()
    if provider in ("anthropic", "claude"):
        return AnthropicProvider(
            api_key=config.resolved_api_key(),
            base_url=config.resolved_base_url(),
            model=config.resolved_model(),
        )
    if provider in ("openai", "openai-compat", "openai-compatible"):
        return OpenAIProvider(
            api_key=config.resolved_api_key(),
            base_url=config.resolved_base_url(),
            model=config.resolved_model(),
        )
    raise ValueError(
        f"Unknown provider {config.provider!r}. Use 'anthropic' or 'openai-compat'."
    )
