# -*- coding: utf-8 -*-
"""兼容层
旧代码里用 `from ..llm import LLMRouter`。
保留一个同名模块，将请求转发到 `llms.LLMRouter`。
"""
from __future__ import annotations

try:
    from .llms import LLMRouter  # 路由器在 llms/__init__.py 中定义
except Exception as e:
    raise ImportError(
        "无法从 service.QueryEngine.llms 导入 LLMRouter，请确认 llms/__init__.py 已定义并可导入。"
    ) from e

__all__ = ["LLMRouter"]
