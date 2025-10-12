# -*- coding: utf-8 -*-
"""
Forum 日志读取工具（项目内专用版）
- 从 logs/forum.log 读取最新的 [HOST] 发言，并可格式化注入到提示词
- 兼容以下时间戳两种格式：
  [HH:MM:SS] [HOST] ...
  [YYYY-MM-DD HH:MM:SS] [HOST] ...
- 仅读取日志尾部，避免大文件整读造成卡顿
"""

import re
from pathlib import Path
from typing import Optional, List, Dict
import logging

logger = logging.getLogger(__name__)

# 匹配 [时间] [HOST] 内容
RE_HOST = re.compile(
    r'\[(?:\d{4}-\d{2}-\d{2}\s+)?\d{2}:\d{2}:\d{2}\]\s*\[HOST\]\s*(.+)'
)
# 匹配 [时间] [INSIGHT|MEDIA|QUERY] 内容
RE_AGENT = re.compile(
    r'\[(?:\d{4}-\d{2}-\d{2}\s+)?\d{2}:\d{2}:\d{2}\]\s*\[(INSIGHT|MEDIA|QUERY)\]\s*(.+)'
)


def _read_tail_lines(p: Path, max_bytes: int = 64 * 1024) -> List[str]:
    """仅读取日志尾部，避免整文件加载"""
    if not p.exists():
        return []
    try:
        size = p.stat().st_size
        with p.open('rb') as f:
            if size > max_bytes:
                f.seek(-max_bytes, 2)  # 相对文件末尾定位
            data = f.read()
        return data.decode('utf-8', errors='ignore').splitlines()
    except Exception as e:
        logger.error(f"读取日志尾部失败: {e}")
        return []


def get_latest_host_speech(log_dir: str = "logs") -> Optional[str]:
    """
    获取 forum.log 中最新的 [HOST] 发言正文；若无返回 None
    """
    forum_log_path = Path(log_dir) / "forum.log"
    lines = _read_tail_lines(forum_log_path)
    host_speech = None
    for line in reversed(lines):  # 从末尾往前找最新一条
        m = RE_HOST.match(line)
        if m:
            host_speech = (m.group(1) or "").replace('\\n', '\n').strip()
            break
    if host_speech:
        logger.info(f"找到最新 HOST 发言，{len(host_speech)} 字")
    else:
        logger.debug("未找到 HOST 发言")
    return host_speech


def get_all_host_speeches(log_dir: str = "logs") -> List[Dict[str, str]]:
    """
    获取 forum.log 中所有 [HOST] 发言（仅做工具备用）
    """
    forum_log_path = Path(log_dir) / "forum.log"
    lines = _read_tail_lines(forum_log_path, max_bytes=4 * 1024 * 1024)
    out: List[Dict[str, str]] = []
    for line in lines:
        m = RE_HOST.match(line)
        if m:
            content = (m.group(1) or "").replace('\\n', '\n').strip()
            out.append({"timestamp": "", "content": content})
    return out


def get_recent_agent_speeches(log_dir: str = "logs", limit: int = 5) -> List[Dict[str, str]]:
    """
    获取最近的 Agent（INSIGHT/MEDIA/QUERY）发言（不含 HOST）
    """
    forum_log_path = Path(log_dir) / "forum.log"
    lines = _read_tail_lines(forum_log_path)
    items: List[Dict[str, str]] = []
    for line in reversed(lines):
        m = RE_AGENT.match(line)
        if m:
            agent, content = m.group(1), m.group(2)
            items.append({
                "timestamp": "",
                "agent": agent,
                "content": (content or "").replace('\\n', '\n').strip()
            })
            if len(items) >= limit:
                break
    items.reverse()
    return items


def format_host_speech_for_prompt(host_speech: str) -> str:
    """
    将 HOST 发言包装成可直接拼接进提示词的片段（System/前缀）
    """
    if not host_speech:
        return ""
    return (
        "### 论坛主持人最新总结\n"
        "以下为主持人对多代理讨论的最新引导，请在你的回答/研究/报告中优先参考：\n\n"
        f"{host_speech}\n\n---\n"
    )
