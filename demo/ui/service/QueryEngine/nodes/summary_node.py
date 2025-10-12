# -*- coding: utf-8 -*-
"""
总结节点实现（健壮增强版）
负责根据搜索结果生成和更新段落内容
- 保留 JSON 结构输出期望，失败时优雅回退到纯文本
- 兼容多种入参键名（title/paragraph_title、search_query/query 等）
- 继续使用 text_processing 的全部能力（不阉割）
"""

from __future__ import annotations

import json
from typing import Dict, Any, List, Optional, Tuple, Union
from json.decoder import JSONDecodeError

from .base_node import StateMutationNode
from ..state.state import State
from ..prompts import SYSTEM_PROMPT_FIRST_SUMMARY, SYSTEM_PROMPT_REFLECTION_SUMMARY
from ..utils.text_processing import (
    remove_reasoning_from_output,
    clean_json_tags,
    extract_clean_response,
    fix_incomplete_json,
    format_search_results_for_prompt,
)

# ---- forum_reader（可选） ----
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from utils.forum_reader import get_latest_host_speech, format_host_speech_for_prompt
    FORUM_READER_AVAILABLE = True
except Exception:
    FORUM_READER_AVAILABLE = False
    print("警告: 无法导入forum_reader模块，将跳过HOST发言读取功能")


# ---------------- 工具函数 ----------------
def _coerce_input_to_dict(input_data: Any) -> Dict[str, Any]:
    """允许字符串 JSON 或 dict，其他类型报错"""
    if isinstance(input_data, dict):
        return dict(input_data)  # 浅拷贝
    if isinstance(input_data, str):
        try:
            return json.loads(input_data)
        except JSONDecodeError as e:
            raise ValueError(f"输入字符串不是合法 JSON：{e}")
    raise ValueError("输入数据必须是 dict 或 JSON 字符串")


def _map_fields_loose(d: Dict[str, Any]) -> Tuple[str, str, str, List[Any]]:
    """
    宽松字段映射：
    - 标题：title / paragraph_title / heading
    - 内容：content / paragraph_content / content_hint / description
    - 查询：search_query / query / user_query
    - 结果：search_results / results / items
    """
    title = (d.get("title")
             or d.get("paragraph_title")
             or d.get("heading")
             or "")
    content = (d.get("content")
               or d.get("paragraph_content")
               or d.get("content_hint")
               or d.get("description")
               or "")
    search_query = (d.get("search_query")
                    or d.get("query")
                    or d.get("user_query")
                    or "")

    # 统一为字符串
    title = title.strip() if isinstance(title, str) else str(title)
    content = content.strip() if isinstance(content, str) else str(content)
    search_query = search_query.strip() if isinstance(search_query, str) else str(search_query)

    raw_results = d.get("search_results")
    if raw_results is None:
        raw_results = d.get("results", d.get("items", []))
    if not isinstance(raw_results, list):
        # 兜底：不是 list 的话，尽量包一下
        raw_results = [raw_results]

    return title, content, search_query, raw_results


def _attach_host_if_available(payload: Dict[str, Any]) -> str:
    """
    若 forum_reader 可用，拼接 HOST 发言在最前；随后放入 JSON 负载。
    返回最终要传给 LLM 的 user_prompt 文本（HOST + JSON）
    """
    message = json.dumps(payload, ensure_ascii=False)
    if FORUM_READER_AVAILABLE:
        try:
            host = get_latest_host_speech()
            if host:
                formatted = format_host_speech_for_prompt(host)
                return f"{formatted}\n{message}"
        except Exception:
            # 静默失败，不影响主流程
            pass
    return message


def _stringify(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)


def _postprocess_output(output: Union[str, Dict[str, Any], List[Any]], json_key: str) -> str:
    """
    对 LLM 输出进行完整的清洗与解析（可接收 str/dict/list）：
    - 去掉推理/标签
    - 先尝试解析 JSON；失败则尝试修复；再失败则回退到清理后的纯文本
    - 如能解析 JSON，从 json_key（如 'paragraph_latest_state'）里取正文
    """
    if output is None:
        return "模型未返回内容"

    # 已经是结构化（dict/list）
    if isinstance(output, (dict, list)):
        data = output
        fallback_text = _stringify(output)
    else:
        # 字符串路径：清洗 → 提取主体 → 解析/修复
        cleaned: Any = remove_reasoning_from_output(output)
        cleaned = clean_json_tags(cleaned)

        # extract_clean_response 可能返回 dict/list/str
        cleaned = extract_clean_response(cleaned)

        if isinstance(cleaned, (dict, list)):
            data = cleaned
            fallback_text = _stringify(cleaned)
        else:
            fallback_text = (cleaned or "").strip()
            # 先直接解析
            try:
                data = json.loads(cleaned)
            except Exception:
                # 试修复
                fixed = fix_incomplete_json(cleaned) or ""
                if fixed:
                    try:
                        data = json.loads(fixed)
                    except Exception:
                        return fallback_text
                else:
                    return fallback_text

    # 从 data 提取正文
    if isinstance(data, dict):
        body = data.get(json_key)
        if isinstance(body, str) and body.strip():
            return body.strip()
        # 某些模型把最终文本放在 "text" / "content"
        for k in ("text", "content"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()

    if isinstance(data, list) and data:
        # 常见情况：单元素 list[dict]
        first = data[0]
        if isinstance(first, dict):
            body = first.get(json_key)
            if isinstance(body, str) and body.strip():
                return body.strip()
            for k in ("text", "content"):
                v = first.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        # list[str] 的兜底
        if all(isinstance(x, str) for x in data):
            joined = "\n".join([x.strip() for x in data if x.strip()])
            if joined:
                return joined

    # JSON 可解析但字段缺失时，也回退到纯文本
    return fallback_text


def _inject_formatted_results(payload: Dict[str, Any]) -> None:
    """
    在不覆盖原始 search_results 的前提下，增补一个“格式化视图”，
    便于模型稳定地产出 JSON；失败则忽略
    """
    try:
        _, _, _, results = _map_fields_loose(payload)
        formatted = format_search_results_for_prompt(results)
        if formatted:
            payload["search_results_formatted"] = formatted
    except Exception:
        # 宽容：格式化失败不应阻断主流程
        pass


# ---------------- 节点实现 ----------------
class FirstSummaryNode(StateMutationNode):
    """根据搜索结果生成段落首次总结的节点"""

    def __init__(self, llm_client):
        super().__init__(llm_client, "FirstSummaryNode")

    def validate_input(self, input_data: Any) -> bool:
        try:
            d = _coerce_input_to_dict(input_data)
            title, content, search_query, results = _map_fields_loose(d)
            return bool(title and search_query and isinstance(results, list))
        except Exception:
            return False

    def run(self, input_data: Any, **kwargs) -> str:
        """
        调用 LLM 生成“首次总结”
        - 期望模型返回 JSON，键为 paragraph_latest_state
        - 若非 JSON 或修复失败，则回退到纯文本（已清洗）
        """
        if not self.validate_input(input_data):
            raise ValueError("输入数据格式错误：缺少必要字段（title/search_query/search_results）")

        data = _coerce_input_to_dict(input_data)

        # 增补 formatted 视图，提升 JSON 输出稳定性（不覆盖原字段）
        _inject_formatted_results(data)

        # 最终传入的 user_prompt：可选 HOST + JSON 负载
        message = _attach_host_if_available(data)

        self.log_info("[FirstSummaryNode] 正在生成首次段落总结")
        response = self.llm_client.invoke(
            SYSTEM_PROMPT_FIRST_SUMMARY,
            message,
            max_tokens=8192,
        )
        # 期望 JSON 的键
        processed = _postprocess_output(response, json_key="paragraph_latest_state")
        self.log_info("[FirstSummaryNode] 首次段落总结完成")
        return processed

    def mutate_state(self, input_data: Any, state: State, paragraph_index: int, **kwargs) -> State:
        summary = self.run(input_data, **kwargs)

        if 0 <= paragraph_index < len(state.paragraphs):
            state.paragraphs[paragraph_index].research.latest_summary = summary
            self.log_info(f"已更新段落 {paragraph_index} 的首次总结")
        else:
            raise ValueError(f"段落索引 {paragraph_index} 超出范围")

        state.update_timestamp()
        return state


class ReflectionSummaryNode(StateMutationNode):
    """根据反思搜索结果更新段落总结的节点"""

    def __init__(self, llm_client):
        super().__init__(llm_client, "ReflectionSummaryNode")

    def validate_input(self, input_data: Any) -> bool:
        try:
            d = _coerce_input_to_dict(input_data)
            title, content, search_query, results = _map_fields_loose(d)
            # 反思需要现有的段落状态
            current = d.get("paragraph_latest_state") or d.get("current_summary") or ""
            if not isinstance(current, str):
                current = _stringify(current)
            return bool(title and isinstance(results, list) and current.strip())
        except Exception:
            return False

    def run(self, input_data: Any, **kwargs) -> str:
        """
        调用 LLM 生成“反思总结”
        - 期望模型返回 JSON，键为 updated_paragraph_latest_state
        - 若非 JSON 或修复失败，则回退到纯文本（已清洗）
        """
        if not self.validate_input(input_data):
            raise ValueError("输入数据格式错误：缺少必要字段（title/search_results/paragraph_latest_state）")

        data = _coerce_input_to_dict(input_data)

        # 增补 formatted 视图（不覆盖原字段）
        _inject_formatted_results(data)

        # 最终传入的 user_prompt：可选 HOST + JSON 负载
        message = _attach_host_if_available(data)

        self.log_info("[ReflectionSummaryNode] 正在生成反思总结")
        response = self.llm_client.invoke(
            SYSTEM_PROMPT_REFLECTION_SUMMARY,
            message,
            max_tokens=8192,
        )
        processed = _postprocess_output(response, json_key="updated_paragraph_latest_state")
        self.log_info("[ReflectionSummaryNode] 反思总结完成")
        return processed

    def mutate_state(self, input_data: Any, state: State, paragraph_index: int, **kwargs) -> State:
        updated_summary = self.run(input_data, **kwargs)

        if 0 <= paragraph_index < len(state.paragraphs):
            state.paragraphs[paragraph_index].research.latest_summary = updated_summary
            # 若有反思计数器则自增（保持兼容，存在才调用）
            try:
                state.paragraphs[paragraph_index].research.increment_reflection()
            except Exception:
                pass
            self.log_info(f"已更新段落 {paragraph_index} 的反思总结")
        else:
            raise ValueError(f"段落索引 {paragraph_index} 超出范围")

        state.update_timestamp()
        return state
