"""LLM provider identifiers."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["LLMProvider"]


class LLMProvider(StrEnum):
    """Supported LLM providers.

    Adding an ``OpenAIProvider`` is a two step change: add ``OPENAI = "openai"``
    here and register the class in :class:`~src.stores.llm.LLMFactory`. No
    service, controller or route needs to change (Dependency Inversion).
    """

    GEMINI = "gemini"
    MOCK = "mock"
