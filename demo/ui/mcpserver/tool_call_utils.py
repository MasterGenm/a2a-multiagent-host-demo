# -*- coding: utf-8 -*-
"""
tool_call_utils.py
- 负责从模型输出中解析 MCP 工具调用 JSON
- 兼容 param_name: "a:7, b:5" -> {"a":7,"b":5}
- 统一执行 MCP: unified_call(service_name, tool_name, **args)
"""
from __future__ import annotations
import json
import re
from typing import Any, Dict, List

# ---- 允许中/英括号与代码块里出现的 JSON ----
# 修复正则表达式，移除不支持的(?R)递归语法
JSON_BLOCK_PATTERN = re.compile(
    r"(?P<json>\{[^{}]*\})",
    re.DOTALL | re.MULTILINE
)

def _safe_json_loads(s: str) -> Any:
    try:
        return json.loads(s)
    except Exception:
        return None

def _coerce_scalar(v: str):
    """把字符串尝试转成 int/float/bool/null，否则原样返回字符串"""
    vs = v.strip()
    if vs.lower() == "true":
        return True
    if vs.lower() == "false":
        return False
    if vs.lower() == "null":
        return None
    # 数字
    try:
        if re.fullmatch(r"[+-]?\d+", vs):
            return int(vs)
        if re.fullmatch(r"[+-]?\d+\.\d+", vs):
            return float(vs)
    except Exception:
        pass
    return v

def _maybe_split_param_name_to_args(tool_args: Dict[str, Any]) -> Dict[str, Any]:
    """
    兼容老式: {"param_name": "a:7, b:5"} -> {"a":7,"b":5}
    """
    if "param_name" in tool_args and isinstance(tool_args["param_name"], str):
        text = tool_args["param_name"]
        # 允许中文逗号
        parts = re.split(r"[，,]", text)
        for p in parts:
            if ":" in p:
                k, v = p.split(":", 1)
                k = k.strip()
                v = _coerce_scalar(v)
                if k:
                    tool_args[k] = v
        del tool_args["param_name"]
    return tool_args

def _normalize_call_obj(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    统一出: {"service_name": str, "tool_name": str, "args": dict}
    允许传入: agentType/mcp 忽略
    """
    service_name = obj.get("service_name") or obj.get("service") or obj.get("svc")
    tool_name = obj.get("tool_name") or obj.get("tool") or obj.get("name")
    if not service_name or not tool_name:
        raise ValueError("缺少 service_name 或 tool_name 字段。")

    # 提取 args（排除已知元字段）
    args = {k: v for k, v in obj.items()
            if k not in ("agentType", "service_name", "service", "svc", "tool_name", "tool", "name")}

    # 兼容 param_name 聚合
    args = _maybe_split_param_name_to_args(args)

    return {
        "service_name": service_name,
        "tool_name": tool_name,
        "args": args
    }

def parse_tool_calls(text: str) -> List[Dict[str, Any]]:
    """
    从模型回复里解析出一个或多个工具调用 JSON。
    - 优先抓 {...} JSON 块
    - 兼容代码块/自然语言包裹
    - 仅收集 agentType=mcp 或未声明但字段齐全的对象
    """
    calls: List[Dict[str, Any]] = []

    if not text or not isinstance(text, str):
        return calls

    # 1) 先直接尝试整体就是一个 JSON
    obj = _safe_json_loads(text)
    if isinstance(obj, dict):
        try:
            calls.append(_normalize_call_obj(obj))
            return calls
        except Exception:
            pass
    elif isinstance(obj, list):
        for it in obj:
            if isinstance(it, dict):
                try:
                    calls.append(_normalize_call_obj(it))
                except Exception:
                    continue
        if calls:
            return calls

    # 2) 提取文本里的所有 JSON 大括号块
    for m in JSON_BLOCK_PATTERN.finditer(text):
        js = m.group("json")
        val = _safe_json_loads(js)
        if isinstance(val, dict):
            # 仅 mcp / 或者字段齐全
            agent_type = str(val.get("agentType", "")).lower()
            if agent_type in ("mcp", ""):
                try:
                    calls.append(_normalize_call_obj(val))
                except Exception:
                    continue

    return calls

def execute_tool_calls(calls: List[Dict[str, Any]]) -> str:
    """
    逐个执行工具调用，并把结果串成可读文本返回。
    依赖 mcp_manager.unified_call(service_name, tool_name, **args)
    """
    if not calls:
        return "未发现可执行的工具调用。"

    # 延迟导入，避免循环依赖
    try:
        # 使用相对导入修复导入问题
        from . import mcp_manager  # 当前文件与 mcp_manager.py 在同一目录
    except Exception as e:
        return f"错误：无法导入 mcp_manager，{e}"

    outputs = []
    for call in calls:
        svc = call["service_name"]
        tool = call["tool_name"]
        args = call.get("args", {}) or {}
        try:
            resp = mcp_manager.unified_call(svc, tool, **args)
            # 允许协程
            if hasattr(resp, "__await__"):
                import asyncio
                loop = None
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
                    # 有正在运行的 loop：开任务等结果
                    resp = asyncio.run_coroutine_threadsafe(resp, loop).result()
                else:
                    resp = asyncio.run(resp)
            outputs.append(f"【工具 {tool}@{svc}】返回: {json.dumps(resp, ensure_ascii=False)}")
        except Exception as e:
            outputs.append(f"【工具 {tool}@{svc}】执行失败: {e}")

    return "\n".join(outputs)