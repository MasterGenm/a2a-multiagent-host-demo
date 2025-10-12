# -*- coding: utf-8 -*-
"""
LLM 调用模块（QueryEngine）
- 暴露各具体 LLM 实现
- 提供 LLMRouter（支持 zhipu / openai / siliconflow）
- 已移除 DeepSeek
"""
from __future__ import annotations
import os
from typing import Optional

from .base import BaseLLM
from .openai_llm import OpenAILLM

try:
    from .zhipu_llm import ZhipuLLM  # 可选
except Exception:
    ZhipuLLM = None  # 允许无智谱依赖时仍可 import

try:
    from .silicon_llm import SiliconFlowLLM  # 新增：硅基流动
except Exception:
    SiliconFlowLLM = None  # 允许未安装时仍可 import


class LLMRouter:
    """
    简单 LLM 路由器（不含 DeepSeek）
    优先级：
      1) 入参 provider/model
      2) 环境变量：QUERYENGINE_LLM_PROVIDER / DEFAULT_LLM_PROVIDER / NAGA_PROVIDER
      3) 缺省：若可用走 zhipu，否则 openai
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ):
        prov = (
            provider
            or os.getenv("QUERYENGINE_LLM_PROVIDER")
            or os.getenv("DEFAULT_LLM_PROVIDER")
            or os.getenv("NAGA_PROVIDER")
            or ""
        ).strip().lower()

        if not prov:
            prov = "zhipu" if ZhipuLLM is not None else "openai"

        # 归一化别名（不再把 siliconflow 映射到 deepseek）
        aliases = {
            "glm": "zhipu", "bigmodel": "zhipu", "zhipuai": "zhipu",
            "oai": "openai", "azure": "openai", "azure-openai": "openai",
            "qwen": "siliconflow", "qwen2": "siliconflow", "qwen3": "siliconflow",
            "silicon": "siliconflow", "siliconflow": "siliconflow",
        }
        prov = aliases.get(prov, prov)

        self.provider = prov
        self.model = (
            model
            or os.getenv("QUERYENGINE_LLM_MODEL")
            or os.getenv("QUERY_MODEL_NAME")
            or os.getenv("NAGA_MODEL_NAME")
            or None
        )
        self.temperature = float(
            temperature if temperature is not None
            else os.getenv("QUERYENGINE_LLM_TEMPERATURE") or 0.3
        )

        # 实例化
        if self.provider == "zhipu" and ZhipuLLM is not None:
            try:
                self._client: BaseLLM = ZhipuLLM(model_name=self.model, temperature=self.temperature)  # type: ignore
            except TypeError:
                self._client = ZhipuLLM(model=self.model, temperature=self.temperature)  # 兼容另一种签名  # type: ignore

        elif self.provider == "siliconflow" and SiliconFlowLLM is not None:
            base_url = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
            api_key = os.getenv("SILICONFLOW_API_KEY", "")
            model_name = self.model or os.getenv("SILICONFLOW_MODEL") or "Qwen/Qwen3-8B"
            if not api_key:
                raise RuntimeError("SILICONFLOW_API_KEY 未设置，无法初始化 SiliconFlowLLM")
            self._client = SiliconFlowLLM(
                api_key=api_key,
                model_name=model_name,
                base_url=base_url,
                temperature=self.temperature,
            )  # type: ignore

        else:
            # 默认走 OpenAI
            self._client = OpenAILLM(model_name=self.model, temperature=self.temperature)

    def get_client(self) -> BaseLLM:
        return self._client

    def invoke(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        return self._client.invoke(system_prompt, user_prompt, **kwargs)


__all__ = ["BaseLLM", "OpenAILLM", "ZhipuLLM", "SiliconFlowLLM", "LLMRouter"]
