# service/ReportEngine/llms/zhipu_llm.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Optional, Iterable

from openai import OpenAI  # openai>=1.x
from .base import BaseLLM


_ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"


class ZhipuLLM(BaseLLM):
    """
    智谱 OpenAI 兼容 SDK 封装
    满足 BaseLLM 的抽象方法：
      - get_default_model()
      - invoke(prompt, system_prompt, temperature)
    可选提供 stream()
    """

    def __init__(
        self,
        api_key: str,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
    ):
        if not api_key:
            raise ValueError("ZhipuLLM 需要 api_key")
        self.api_key = api_key
        self.model_name = model_name or self.get_default_model()
        self.base_url = base_url or _ZHIPU_BASE
        self.timeout = timeout

        # OpenAI 兼容客户端
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    # ---- BaseLLM 必需 ----
    def get_default_model(self) -> str:
        # ReportEngine 内部缺省，我们用速度快的 flash
        return "glm-4.5-flash"

    def invoke(
        self,
        prompt: str,
        system_prompt: Optional[str] = "You are a helpful assistant.",
        temperature: float = 0.7,
        **kwargs,
    ) -> str:
        """
        非流式一次性调用，返回字符串
        """
        resp = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt or ""},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
        )
        content = resp.choices[0].message.content or ""
        return content.strip()

    # ---- 可选：流式 ----
    def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = "You are a helpful assistant.",
        temperature: float = 0.7,
        **kwargs,
    ) -> Iterable[str]:
        """
        需要时可被上层用于流式；ReportEngine 默认用非流式即可
        """
        with self.client.chat.completions.stream(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt or ""},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
        ) as stream:
            for event in stream:
                try:
                    delta = getattr(event, "delta", None)
                    if delta and getattr(delta, "content", None):
                        yield delta.content
                except Exception:
                    # 兼容不同 SDK 的事件结构
                    pass

    # ---- 便于日志 & 调试 ----
    def get_model_info(self) -> str:
        return f"zhipu:{self.model_name}"
