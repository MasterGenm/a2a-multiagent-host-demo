# -*- coding: utf-8 -*-
"""
Search nodes (robust version)
- 修复 run() 里把输入 dict 覆盖为 list 导致的 AttributeError
- 兼容输入既可为 dict 也可为纯文本
- 输出严格为 {search_query, search_tool, reasoning, [start_date], [end_date]}
"""

from __future__ import annotations
import json
import re
from typing import Any, Dict, Optional

from .base_node import BaseNode


def _extract_json_best_effort(text: str) -> Dict[str, Any]:
    """尽量从模型输出里捕捉到一个 JSON 对象"""
    if not text:
        return {}
    # 去掉围栏
    text = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.I).strip()
    text = re.sub(r"```$", "", text, flags=re.M).strip()

    # 直接尝试
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 退而求其次：抓第一个 { ... } 块
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:
            return {}
    return {}


def _normalize_output(obj: Dict[str, Any]) -> Dict[str, Any]:
    """把模型输出补齐/归一化为我们要的键"""
    out: Dict[str, Any] = {}
    # search_query
    out["search_query"] = (
        obj.get("search_query")
        or obj.get("query")
        or obj.get("q")
        or ""
    ).strip()

    # search_tool
    tool = (obj.get("search_tool") or "").strip()
    allowed = {
        "basic_search_news",
        "deep_search_news",
        "search_news_last_24_hours",
        "search_news_last_week",
        "search_images_for_news",
        "search_news_by_date",
    }
    if tool not in allowed:
        tool = "basic_search_news"
    out["search_tool"] = tool

    # reasoning
    out["reasoning"] = (obj.get("reasoning") or obj.get("why") or "").strip()

    # 可选时间
    sd = (obj.get("start_date") or "").strip()
    ed = (obj.get("end_date") or "").strip()
    if sd and ed:
        out["start_date"] = sd
        out["end_date"] = ed

    return out


class FirstSearchNode(BaseNode):
    """首次检索：根据段落标题/说明生成检索词和工具"""

    SYSTEM_PROMPT = (
        "你是研究助理。根据用户给出的段落标题与说明，输出一个 JSON，"
        "字段包括：search_query(必填), search_tool(必填，"
        "basic_search_news|deep_search_news|search_news_last_24_hours|search_news_last_week|"
        "search_images_for_news|search_news_by_date 之一), reasoning(简要), "
        "start_date/end_date(可选，YYYY-MM-DD)。不要输出除 JSON 以外的任何文字。"
    )

    def run(self, input_data: Any, **kwargs) -> Dict[str, Any]:
        """
        input_data: dict 或 str
          - dict: {title:str, content:str, ...}
          - str: 直接视为需求描述
        """
        # ——坚决不要覆盖 input_data 这个变量——
        if isinstance(input_data, dict):
            title = (input_data.get("title") or "").strip()
            content = (input_data.get("content") or "").strip()
            msg = json.dumps({"title": title, "content": content}, ensure_ascii=False)
        else:
            msg = str(input_data)

        self.log_info("正在生成首次搜索查询")
        raw = self.llm_client.invoke(self.SYSTEM_PROMPT, msg)
        obj = _extract_json_best_effort(raw)
        out = _normalize_output(obj)

        # 兜底：如果 search_query 仍为空，用标题或内容前若干字
        if not out["search_query"]:
            seed = title if isinstance(input_data, dict) else msg
            out["search_query"] = (seed or "科技 趋势 市场 数据").strip()[:80]

        self.log_info(f"生成搜索查询: {out['search_query']}")
        return out


class ReflectionNode(BaseNode):
    """反思检索：基于上一版段落总结，补全缺口"""

    SYSTEM_PROMPT = (
        "你是研究助理。根据给定的“段落最新内容摘要/当前缺口”，输出一个 JSON，"
        "字段包括：search_query(必填), search_tool(必填：同上六选一), "
        "reasoning(简要), start_date/end_date(可选 YYYY-MM-DD)。"
        "只输出 JSON。"
    )

    def run(self, input_data: Any, **kwargs) -> Dict[str, Any]:
        """
        input_data: dict, 期望包含：
          - title: 段落标题
          - content: 原始段落指引
          - paragraph_latest_state: 上一次总结
        """
        if isinstance(input_data, dict):
            payload = {
                "title": (input_data.get("title") or "").strip(),
                "content": (input_data.get("content") or "").strip(),
                "latest": (input_data.get("paragraph_latest_state") or "").strip(),
            }
            msg = json.dumps(payload, ensure_ascii=False)
        else:
            msg = str(input_data)

        self.log_info("正在进行反思并生成新搜索查询")
        raw = self.llm_client.invoke(self.SYSTEM_PROMPT, msg)
        obj = _extract_json_best_effort(raw)
        out = _normalize_output(obj)

        if not out["search_query"]:
            seed = payload["title"] if isinstance(input_data, dict) else msg
            out["search_query"] = (seed or "补充 数据 案例 最新 动态").strip()[:80]

        self.log_info(f"反思生成搜索查询: {out['search_query']}")
        return out
