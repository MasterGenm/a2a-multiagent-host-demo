# -*- coding: utf-8 -*-
# service/QueryEngine/llms/zhipu_llm.py
import os
from typing import Optional, Dict, Any
from openai import OpenAI
from .base import BaseLLM

DEFAULT_ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"

class ZhipuLLM(BaseLLM):
    """Zhipu (GLM) 的 OpenAI 兼容客户端实现，强制使用 bigmodel 网关"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.3,
    ):
        if api_key is None:
            api_key = os.getenv("ZHIPU_API_KEY") or os.getenv("NAGA_API_KEY")
        if not api_key:
            raise RuntimeError("Zhipu LLM 缺少 API Key：请设置 ZHIPU_API_KEY 或 NAGA_API_KEY")

        # 模型选择：允许外部覆盖，但默认 glm-4.5
        model_name = (
            model_name
            or os.getenv("QUERYENGINE_ZHIPU_MODEL")
            or os.getenv("QUERY_MODEL_NAME")
            or os.getenv("NAGA_MODEL_NAME")
            or "glm-4.5"
        )

        # 取环境变量/入参的候选 base，再做强制校验
        candidate = (
            base_url
            or os.getenv("ZHIPU_BASE_URL")
            or os.getenv("NAGA_BASE_URL")
            or DEFAULT_ZHIPU_BASE
        )

        # ✅ 强制走 bigmodel：任何非 open.bigmodel.cn 的地址一律改回官方网关
        if "open.bigmodel.cn" not in (candidate or ""):
            print(f"[QueryEngine.ZhipuLLM] WARNING: override non-Zhipu base_url={candidate} -> {DEFAULT_ZHIPU_BASE}")
            candidate = DEFAULT_ZHIPU_BASE

        super().__init__(api_key=api_key, model_name=model_name)
        self.temperature = temperature
        self.base_url = candidate
        self.default_model = model_name
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

        print(f"[QueryEngine.ZhipuLLM] base_url={self.base_url} model={self.default_model}")

    def get_default_model(self) -> str:
        return "glm-4.5"

    def invoke(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        temp = kwargs.get("temperature", self.temperature)
        max_tokens = kwargs.get("max_tokens", 8192)
        resp = self.client.chat.completions.create(
            model=self.default_model,
            messages=[
                {"role": "system", "content": system_prompt or ""},
                {"role": "user",   "content": user_prompt or ""},
            ],
            temperature=temp,
            max_tokens=max_tokens,
        )
        content = (resp.choices[0].message.content or "").strip()
        return self.validate_response(content)

    def get_model_info(self) -> Dict[str, Any]:
        return {"provider": "zhipu", "model": self.default_model, "api_base": self.base_url}
