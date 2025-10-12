# -*- coding: utf-8 -*-
"""
mcp_registry.py — 统一注册中心（兼容别名/大小写；复制即用）
- 加载"本地 MCP Agent"（扫描 mcpserver/**/agent-manifest.json）
- 加载"远程 MCP 服务"（读取 system/mcp_servers.json / demo/ui/mcp_servers.json / 环境变量 MCP_SERVERS_JSON）
- 提供：auto_register_mcp / refresh_registry / get_all_services_info / get_service / has_service
"""
from __future__ import annotations
import importlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

MCP_REGISTRY: Dict[str, Any] = {}   # 原始名 -> 实例/代理
_LOOKUP: Dict[str, str] = {}        # 归一化名/别名 -> 原始名
_MANIFESTS: List[Path] = []
_REMOTE_CFG_PATHS: List[Path] = []

# -----------------------------
# 基本路径
# -----------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent  # demo/ui
MCP_DIR = ROOT_DIR / "mcpserver"
SYS_DIR = ROOT_DIR / "system"

# -----------------------------
# 常用别名（大小写不敏感）
# -----------------------------
ALIASES = {
    # WebSearch
    "websearch": "searxng搜索",
    "web_search": "searxng搜索",
    "阿里云百炼_联网搜索": "searxng搜索",
    # WebParser
    "webparser": "WebParser",
    "web_parser": "WebParser",
    "阿里云百炼_网页解析": "WebParser",
    # 财报指标
    "bz-finance": "bz-finance",
    "博众财报": "bz-finance",
    "阿里云百炼_博众财报指标": "bz-finance",
    # 且慢
    "qieman": "Qieman",
    "且慢": "Qieman",
    "阿里云百炼_且慢": "Qieman",
    # tzyk demo
    "tzyk": "tzyk-mcp-server-sse",
    "tzyk-mcp-server-sse": "tzyk-mcp-server-sse",
    "tzyk demo finance": "tzyk-mcp-server-sse",
}

def _norm(s: str) -> str:
    return (s or "").strip().lower()

def _lookup_build() -> None:
    """把已注册的服务名、display_name 和 ALIASES 建一个总索引 _LOOKUP"""
    _LOOKUP.clear()
    for original_key, svc in MCP_REGISTRY.items():
        # 原始名
        _LOOKUP[_norm(original_key)] = original_key
        # 展示名
        disp = getattr(svc, "display_name", None)
        if isinstance(disp, str) and disp:
            _LOOKUP[_norm(disp)] = original_key
    # 别名 -> 映射到已存在的原始名
    for alias, target in ALIASES.items():
        key = None
        # 目标可能是原始key，也可能是展示名
        if _norm(target) in _LOOKUP:
            key = _LOOKUP[_norm(target)]
        elif target in MCP_REGISTRY:
            key = target
        if key:
            _LOOKUP[_norm(alias)] = key

def get_service(name: str) -> Any | None:
    """兼容大小写 / 中文别名 / 展示名"""
    if not name:
        return None
    # 直接命中
    if name in MCP_REGISTRY:
        return MCP_REGISTRY[name]
    # 归一化命中
    key = _LOOKUP.get(_norm(name))
    if key:
        return MCP_REGISTRY.get(key)
    return None