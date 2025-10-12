# -*- coding: utf-8 -*-
"""
QueryEngine.utils 聚合出口
- 对外稳定暴露：Config, load_config, format_search_results_for_prompt
- LLM 提供商实现使用可选导入（缺失时不阻断）
"""

# 1) 配置对象 / 加载器 —— 必须可用
from .config import Config, load_config  # noqa: F401

# 2) 文本处理工具 —— 至少要导出 format_search_results_for_prompt
try:
    from .text_processing import format_search_results_for_prompt  # type: ignore
except Exception:
    # 兜底：如果你的 text_processing 暂无该函数，这里给个最小实现
    def format_search_results_for_prompt(results):
        """
        results: List[dict] 或任意可迭代，尽力从每条里取 title / url / published_date
        """
        lines = []
        for i, item in enumerate(results or [], 1):
            title = (item.get("title") if isinstance(item, dict) else None) or str(item)
            url = item.get("url") if isinstance(item, dict) else ""
            pub = item.get("published_date") if isinstance(item, dict) else ""
            extra = f" （{pub}）" if pub else ""
            lines.append(f"{i}. {title}{extra}\n   {url}")
        return "\n".join(lines)

# 3) LLM 抽象基类 —— 建议保留（上一轮我们已加了 utils/base.py 垫片）
try:
    from .base import BaseLLM  # type: ignore
except Exception:
    BaseLLM = None  # 允许缺失

# 4) 可选的具体 LLM 实现（若文件不存在，不阻断）
try:
    from .deepseek import DeepseekLLM  # type: ignore
except Exception:
    DeepseekLLM = None  # type: ignore
try:
    from .openai_llm import OpenAILLM  # type: ignore
except Exception:
    OpenAILLM = None  # type: ignore
try:
    from .zhipu_llm import ZhipuLLM  # type: ignore
except Exception:
    ZhipuLLM = None  # type: ignore

# 5) 对外暴露
__all__ = [
    "Config",
    "load_config",
    "format_search_results_for_prompt",
    "BaseLLM",
    "DeepseekLLM",
    "OpenAILLM",
    "ZhipuLLM",
]
