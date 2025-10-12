# -*- coding: utf-8 -*-
from pathlib import Path
import json
from typing import List, Dict, Any, Optional
from service.utils.path_utils import get_query_dir

def _pick_latest_state_file() -> Optional[Path]:
    qdir = get_query_dir()
    candidates = sorted(qdir.glob("state_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None

def assemble_materials_from_state_dict(state: Dict[str, Any], max_links_per_section: int = 6) -> str:
    """把 State 字典组装成“研究材料”（Markdown）"""
    paras: List[Dict[str, Any]] = state.get("paragraphs", [])
    if not paras:
        return ""
    sections = []
    # 有些 state 没 order 字段，这里默认按索引顺序
    for idx, p in enumerate(sorted(paras, key=lambda x: x.get("order", idx))):
        title = (p.get("title") or f"段落{idx+1}").strip()
        research = p.get("research") or {}
        latest = (research.get("latest_summary") or p.get("content") or "").strip()
        # 抽取链接
        urls = []
        for item in (research.get("search_history") or []):
            u = item.get("url")
            if u and u not in urls:
                urls.append(u)
        urls = urls[:max_links_per_section]
        sec = [f"### {title}", latest or "（本段暂无可用内容）"]
        if urls:
            sec.append("参考链接：")
            sec.extend([f"- {u}" for u in urls])
        sections.append("\n".join(sec))
    return "\n\n".join(sections)

def build_materials_markdown_from_latest_state() -> str:
    """载入最新 state_*.json 并返回 Markdown 材料"""
    sp = _pick_latest_state_file()
    if not sp:
        return ""
    data = json.loads(sp.read_text(encoding="utf-8"))
    return assemble_materials_from_state_dict(data)
