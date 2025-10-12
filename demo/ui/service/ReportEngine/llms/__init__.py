# service/ReportEngine/llms/__init__.py
# -*- coding: utf-8 -*-

from .base import BaseLLM
from .gemini_llm import GeminiLLM
try:
    from .zhipu_llm import ZhipuLLM  # 新增
except Exception:
    ZhipuLLM = None  # 允许没有 openai 时也能 import 通过

__all__ = ["BaseLLM", "GeminiLLM", "ZhipuLLM"]
