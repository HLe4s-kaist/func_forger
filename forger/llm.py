"""LLM provider abstraction.

Two providers share one interface so the rest of the code is provider-agnostic:

* :class:`AnthropicProvider` -- proprietary Claude models.
* :class:`OpenAIProvider` -- anything behind an OpenAI-compatible Chat
  Completions endpoint. That covers OpenAI itself **and** open-source /
  self-hosted models served via Ollama, vLLM, LM Studio, Together, Groq, etc.

The SDK is imported lazily inside each provider, so importing this module (or
the package) never requires any third-party SDK to be installed. The SDK is
only needed at the moment an LLM is actually called.
"""

from __future__ import annotations

from typing import Protocol


class LLMProvider(Protocol):
    """A minimal chat-completion interface used by the implementer."""

    def complete(self, system: str, messages: list[dict], *, model: str | None = None) -> str:
        ...


class AnthropicProvider:
    """Claude via the ``anthropic`` SDK (proprieary backend)."""

    def __init__(self, api_key: str | None, base_url: str | None = None, model: str | None = None):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._client = None

    def _ensure(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover - depends on env
                raise RuntimeError(
                    "The `anthropic` SDK is not installed. Install it with `pip install anthropic`."
                ) from e
            kwargs = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = anthropic.Anthropic(**kwargs)

    def complete(self, system: str, messages: list[dict], *, model: str | None = None) -> str:
        self._ensure()
        resp = self._client.messages.create(
            model=model or self._model,
            max_tokens=4096,
            system=system,
            messages=messages,
        )
        return "".join(block.text for block in resp.content if getattr(block, "text", None))


class OpenAIProvider:
    """Any OpenAI-compatible Chat Completions endpoint (proprietary or
    open-source, e.g. Ollama / vLLM / LM Studio)."""

    def __init__(self, api_key: str | None, base_url: str | None = None, model: str | None = None):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._client = None

    def _ensure(self):
        if self._client is None:
            try:
                import openai
            except ImportError as e:  # pragma: no cover - depends on env
                raise RuntimeError(
                    "The `openai` SDK is not installed. Install it with `pip install openai`."
                ) from e
            kwargs = {"api_key": self._api_key or "not-required"}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.OpenAI(**kwargs)

    def complete(self, system: str, messages: list[dict], *, model: str | None = None) -> str:
        self._ensure()
        resp = self._client.chat.completions.create(
            model=model or self._model,
            messages=[{"role": "system", "content": system}, *messages],
            max_tokens=4096,
        )
        return resp.choices[0].message.content or ""


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
