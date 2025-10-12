# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Any, Dict, List, Optional
from openai import OpenAI

class BaseLLM:  # 仅做类型提示；真实 BaseLLM 由 llms/__init__.py 导出
    def invoke(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        raise NotImplementedError
    def get_model_info(self) -> Dict[str, Any]:
        return {}

class SiliconFlowLLM(BaseLLM):
    """
    硅基流动 LLM 适配器（OpenAI 兼容客户端）
    - base_url: https://api.siliconflow.cn/v1
    - 常用模型: "Qwen/Qwen3-8B"
    """
    def __init__(self, api_key: str, model_name: str = "Qwen/Qwen3-8B",
                 base_url: str = "https://api.siliconflow.cn/v1", temperature: float = 0.3):
        self.api_key = api_key
        self.default_model = model_name
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.client = OpenAI(api_key=api_key, base_url=self.base_url)

    def invoke(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        max_tokens = kwargs.get("max_tokens", 4096)
        temperature = kwargs.get("temperature", self.temperature)
        messages = []
        if (system_prompt or "").strip():
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        resp = self.client.chat.completions.create(
            model=self.default_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()

    def get_model_info(self) -> Dict[str, Any]:
        return {"provider": "siliconflow", "model": self.default_model, "api_base": self.base_url}
