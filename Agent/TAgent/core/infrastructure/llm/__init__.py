# -*- coding: utf-8 -*-
"""LLM 适配器集合。"""

from .mock import EchoLLMClient, ScriptedLLMClient
from .openai_compat import OpenAICompatibleClient

__all__ = ["OpenAICompatibleClient", "ScriptedLLMClient", "EchoLLMClient"]
