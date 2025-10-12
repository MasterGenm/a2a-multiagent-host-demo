# -*- coding: utf-8 -*-
"""
文本处理工具函数
用于清理LLM输出、解析JSON等

改动要点（兼容增强而不阉割功能）：
1) 所有“清洗字符串”的函数在接收到 dict/list/None 时，先安全转成字符串（JSON 序列化），避免 re.sub 抛 TypeError。
2) extract_clean_response 保留原有多策略解析/修复能力，但在传入就是 dict/list 时原样返回（零拷贝），避免无谓的序列化/反序列化。
3) 其余工具函数保持原有行为不变。
"""

from __future__ import annotations

import re
import json
from typing import Dict, Any, List, Union, Optional
from json.decoder import JSONDecodeError


# =========================
# 基础：安全的字符串化工具
# =========================
def _ensure_text(obj: Any) -> str:
    """
    将任意对象安全转换为字符串：
    - str -> 原样
    - dict/list -> json.dumps(ensure_ascii=False)
    - None -> ""
    - 其他 -> str(obj)
    """
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (dict, list)):
        try:
            return json.dumps(obj, ensure_ascii=False)
        except Exception:
            # 兜底：强转字符串
            return str(obj)
    return str(obj)


# =========================
# 清洗函数（入参统一先 _ensure_text）
# =========================
def clean_json_tags(text: Any) -> str:
    """
    清理文本中的JSON标签：去除 ```json / ``` 包裹等
    入参允许 str / dict / list / None
    """
    s = _ensure_text(text)

    # 移除 ```json 和 ``` 标签（保留原逻辑）
    s = re.sub(r'```json\s*', '', s)
    s = re.sub(r'```\s*$', '', s)
    s = re.sub(r'```', '', s)

    return s.strip()


def clean_markdown_tags(text: Any) -> str:
    """
    清理文本中的Markdown标签：去除 ```markdown / ``` 包裹等
    入参允许 str / dict / list / None
    """
    s = _ensure_text(text)

    s = re.sub(r'```markdown\s*', '', s)
    s = re.sub(r'```\s*$', '', s)
    s = re.sub(r'```', '', s)

    return s.strip()


def remove_reasoning_from_output(text: Any) -> str:
    """
    移除输出中的推理过程，尽量把前置的说明/推理去掉，直接定位到 JSON 起始处。
    入参允许 str / dict / list / None
    """
    s = _ensure_text(text)

    # 查找JSON开始位置（第一个 { 或 [）
    json_start = -1
    for i, ch in enumerate(s):
        if ch in '{[':
            json_start = i
            break
    if json_start != -1:
        return s[json_start:].strip()

    # 如果没有显式起始，尝试移除常见的推理标识
    patterns = [
        r'(?:reasoning|推理|思考|分析)[:：]\s*.*?(?=\{|\[)',  # 推理部分
        r'(?:explanation|解释|说明)[:：]\s*.*?(?=\{|\[)',   # 解释部分
        r'^.*?(?=\{|\[)',                                   # JSON 前的所有内容
    ]
    for p in patterns:
        s = re.sub(p, '', s, flags=re.IGNORECASE | re.DOTALL)

    return s.strip()


# =========================
# JSON 解析与修复
# =========================
def extract_clean_response(text: Any) -> Union[Dict[str, Any], List[Any], Dict[str, Any]]:
    """
    提取并清理响应中的 JSON 内容。
    - 若传入已是 dict/list，直接返回（不破坏上游语义）。
    - 否则进行：去标签 -> 去推理 -> 尝试 loads -> 尝试修复 -> 尝试提取对象/数组。
    - 全部失败则返回 {"error": "JSON解析失败", "raw_text": cleaned_text}
    """
    # 传入已是结构化对象：直接返回
    if isinstance(text, (dict, list)):
        return text

    # 清理文本
    cleaned_text = clean_json_tags(text)
    cleaned_text = remove_reasoning_from_output(cleaned_text)

    # 1) 直接解析
    try:
        return json.loads(cleaned_text)
    except JSONDecodeError:
        pass

    # 2) 尝试修复不完整 JSON
    fixed_text = fix_incomplete_json(cleaned_text)
    if fixed_text:
        try:
            return json.loads(fixed_text)
        except JSONDecodeError:
            pass

    # 3) 尝试提取对象
    obj_match = re.search(r'\{.*\}', cleaned_text, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group())
        except JSONDecodeError:
            pass

    # 4) 尝试提取数组
    arr_match = re.search(r'\[.*\]', cleaned_text, re.DOTALL)
    if arr_match:
        try:
            return json.loads(arr_match.group())
        except JSONDecodeError:
            pass

    # 5) 全部失败
    print(f"无法解析JSON响应: {cleaned_text[:200]}...")
    return {"error": "JSON解析失败", "raw_text": cleaned_text}


def fix_incomplete_json(text: Any) -> str:
    """
    修复不完整的 JSON 响应，尽可能返回一个可被 json.loads 解析的字符串。
    入参允许 str / dict / list / None（非字符串会先转字符串）
    """
    s = _ensure_text(text)

    # 去掉对象/数组末尾多余逗号
    s = re.sub(r',\s*}', '}', s)
    s = re.sub(r',\s*]', ']', s)

    # 已经是合法 JSON
    try:
        json.loads(s)
        return s
    except JSONDecodeError:
        pass

    # 如果像是被截断造成的大括号/方括号不匹配，做最小化补全
    open_braces = s.count('{')
    close_braces = s.count('}')
    open_brackets = s.count('[')
    close_brackets = s.count(']')

    if open_braces > close_braces:
        s += '}' * (open_braces - close_braces)
    if open_brackets > close_brackets:
        s += ']' * (open_brackets - close_brackets)

    # 再试一次
    try:
        json.loads(s)
        return s
    except JSONDecodeError:
        # 仍然失败，进入更激进修复
        return fix_aggressive_json(s)


def fix_aggressive_json(text: Any) -> str:
    """
    更激进的 JSON 修复：抓取所有“看起来像对象”的片段并拼成数组。
    入参允许 str / dict / list / None（非字符串会先转字符串）
    """
    s = _ensure_text(text)

    # 提取最外层不嵌套的对象片段（容忍轻微噪声）
    # 注意：这是最后的兜底策略，可能丢失上下文顺序，但至少不至于完全失败。
    objects = re.findall(r'\{[^{}]*\}', s)

    if len(objects) >= 2:
        return '[' + ','.join(objects) + ']'
    elif len(objects) == 1:
        return '[' + objects[0] + ']'
    else:
        # 如果一个对象都没有，返回空数组字符串，保证 json.loads 可用
        return '[]'


# =========================
# 结果与状态辅助
# =========================
def update_state_with_search_results(
    search_results: List[Dict[str, Any]],
    paragraph_index: int,
    state: Any
) -> Any:
    """
    将搜索结果更新到状态中（保持原行为）
    """
    if 0 <= paragraph_index < len(getattr(state, "paragraphs", [])):
        current_query = ""
        if search_results:
            # TODO: 如需记录真实查询，可在调用处传入；这里保留“占位推断”
            current_query = "搜索查询"

        state.paragraphs[paragraph_index].research.add_search_results(
            current_query, search_results
        )
    return state


def validate_json_schema(data: Dict[str, Any], required_fields: List[str]) -> bool:
    """
    验证JSON数据是否包含必需字段
    """
    return all(field in data for field in required_fields)


def truncate_content(content: str, max_length: int = 20000) -> str:
    """
    截断内容到指定长度（保持原行为）
    """
    if content is None:
        return ""
    if len(content) <= max_length:
        return content
    truncated = content[:max_length]
    last_space = truncated.rfind(' ')
    if last_space > max_length * 0.8:
        return truncated[:last_space] + "..."
    return truncated + "..."


def format_search_results_for_prompt(
    search_results: List[Dict[str, Any]],
    max_length: int = 20000
) -> List[str]:
    """
    格式化搜索结果用于提示词（保持原行为）
    """
    formatted_results = []
    for result in search_results:
        content = result.get('content', '')
        if content:
            formatted_results.append(truncate_content(content, max_length))
    return formatted_results
