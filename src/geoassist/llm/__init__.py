"""Cliente de LLM compatible con la API de chat completions de OpenAI."""

from __future__ import annotations

from geoassist.llm.base import LLMClient, LLMError
from geoassist.llm.openai_compatible import OpenAICompatibleClient

__all__ = ["LLMClient", "LLMError", "OpenAICompatibleClient"]
