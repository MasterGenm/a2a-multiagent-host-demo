# -*- coding: utf-8 -*-
"""
兼容垫片：
- 若已存在 ..llms.base 则转发；否则提供最小可用的 BaseLLM 抽象基类
"""

try:
    # 如果你后来把基类放在 llms/base.py，这里会自动转发
    from ..llms.base import BaseLLM  # type: ignore
except Exception:
    from abc import ABC, abstractmethod
    from typing import Any, Dict, List, Optional

    class BaseLLM(ABC):
        """最小可用 LLM 抽象基类，确保 imports 不再报错；具体实现交给子类。"""

        def __init__(
            self,
            model: Optional[str] = None,
            api_key: Optional[str] = None,
            base_url: Optional[str] = None,
            temperature: float = 0.3,
            timeout: int = 60,
            **kwargs: Any,
        ) -> None:
            self.model = model
            self.api_key = api_key
            self.base_url = base_url
            self.temperature = temperature
            self.timeout = timeout
            self.extra: Dict[str, Any] = dict(kwargs)

        @abstractmethod
        def chat(
            self,
            system_prompt: str,
            user_prompt: str,
            tools: Optional[List[Dict[str, Any]]] = None,
            **kwargs: Any,
        ) -> str:
            """返回 LLM 文本输出；由具体提供商子类实现。"""
            raise NotImplementedError("Please implement BaseLLM.chat() in a subclass.")
