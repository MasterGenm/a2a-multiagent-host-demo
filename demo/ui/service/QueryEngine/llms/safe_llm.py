# -*- coding: utf-8 -*-
from __future__ import annotations
import os
from typing import Any, Dict, List, Tuple

try:
    from openai import BadRequestError
except Exception:
    BadRequestError = Exception

class BaseLLM:
    def invoke(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        raise NotImplementedError
    def get_model_info(self) -> Dict[str, Any]:
        return {}

def _is_safety_block(err: Exception) -> bool:
    s = str(err)
    if "contentFilter" in s or "不安全或敏感" in s or "1301" in s:
        return True
    return False

def _wrap_with_safety(system_prompt: str, user_prompt: str) -> Tuple[str, str]:
    SAFETY_PREFIX = (
        "【安全提示】你是一名技术研究助手，只做客观、中立、可核验的技术/市场/合规事实性总结；"
        "避免涉及意识形态或国家/政党/政策优劣评价；如涉及监管/政策，仅列举公开事实，不作倾向性判断或号召；"
        "避免煽动、鼓动或动员性表达。"
    )
    sp = (system_prompt or "").strip()
    up = (user_prompt or "").strip()
    sp = (SAFETY_PREFIX + "\n\n" + sp) if sp else SAFETY_PREFIX
    return sp, up

class SafeRouterLLM(BaseLLM):
    """
    - primary: 主模型（通常 zhipu 或 openai）
    - fallback_general: 普通错误时尝试（建议放 openai）
    - fallback_sensitive: 命中风控时才尝试（专用：siliconflow）
    """
    def __init__(self, primary_llm: BaseLLM,
                 fallback_general: List[BaseLLM] | None = None,
                 fallback_sensitive: List[BaseLLM] | None = None):
        self.primary = primary_llm
        self.fallback_general = fallback_general or []
        self.fallback_sensitive = fallback_sensitive or []
        self.safety_mode = (os.getenv("QE_SAFETY_MODE") or "light").lower()  # off/light/strict
        self.fallback_on_sensitive = (os.getenv("QE_FALLBACK_ON_SENSITIVE") or "1").lower() in ("1","true","yes")

    def get_model_info(self) -> Dict[str, Any]:
        info = {"primary": self.primary.get_model_info()}
        if self.fallback_general:
            info["fallback_general"] = [fb.get_model_info() for fb in self.fallback_general]
        if self.fallback_sensitive:
            info["fallback_sensitive"] = [fb.get_model_info() for fb in self.fallback_sensitive]
        return info

    def _maybe_wrap(self, system_prompt: str, user_prompt: str, force: bool = False) -> Tuple[str, str]:
        if force:
            return _wrap_with_safety(system_prompt, user_prompt)
        if self.safety_mode == "strict":
            return _wrap_with_safety(system_prompt, user_prompt)
        if self.safety_mode == "light":
            return system_prompt, user_prompt
        return system_prompt, user_prompt  # off

    def invoke(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        # 1) 首次尝试（strict 模式会带安全前缀；off/light 不带）
        sp0, up0 = self._maybe_wrap(system_prompt, user_prompt, force=False)
        try:
            return self.primary.invoke(sp0, up0, **kwargs)
        except Exception as e:
            if not _is_safety_block(e):
                # 2) 非风控错误 → 尝试通用回退（如 OpenAI）
                for fb in self.fallback_general:
                    try:
                        return fb.invoke(sp0, up0, **kwargs)
                    except Exception:
                        continue
                raise

            # 3) 风控错误 → 对主模型做“降敏前缀”后重试
            sp1, up1 = _wrap_with_safety(system_prompt, user_prompt)
            try:
                return self.primary.invoke(sp1, up1, **kwargs)
            except Exception as e2:
                # 4) 若仍失败 → 只在风控场景启用“敏感回退”（SiliconFlow）
                if self.fallback_on_sensitive and self.fallback_sensitive:
                    for fb in self.fallback_sensitive:
                        try:
                            return fb.invoke(sp1, up1, **kwargs)
                        except Exception:
                            continue
                raise e2
