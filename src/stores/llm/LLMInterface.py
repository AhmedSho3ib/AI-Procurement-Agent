"""The LLM contract every provider must satisfy.

The business/service layer depends on this interface only. Providers own *all*
provider specific details, including how a CrewAI agent handle is obtained.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel

from src.stores.llm.LLMEnums import LLMProvider

__all__ = ["LLMInterface", "StructuredModel"]

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class LLMInterface(ABC):
    """Text + structured generation against a language model provider."""

    #: Identifier of the concrete provider.
    provider: LLMProvider

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The provider-specific model identifier (safe to log)."""

    @abstractmethod
    def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        """Return free-form text for ``prompt``."""

    @abstractmethod
    def generate_structured(
        self,
        prompt: str,
        response_model: type[StructuredModel],
        *,
        system_prompt: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> StructuredModel:
        """Return a validated ``response_model`` instance.

        ``prompt`` carries the instructions; ``context`` carries the structured
        data the model must reason over. Keeping them separate stops prompt
        injection through data (a product title can never rewrite the
        instructions) and lets providers serialise context deterministically.
        """

    @abstractmethod
    def as_agent_llm(self) -> Any:
        """Return the provider-native handle used by CrewAI agents.

        Typed as ``Any`` on purpose: the concrete type is provider specific
        (``crewai.LLM`` today, an OpenAI client tomorrow) and must not leak into
        services or controllers.
        """
