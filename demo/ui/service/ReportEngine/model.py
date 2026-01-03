# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import json, re
from pathlib import Path
from datetime import datetime

@dataclass
class TableData:
    headers: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)

@dataclass
class Figure:
    path: str
    caption: Optional[str] = None
    width_px: Optional[int] = None

@dataclass
class Section:
    title: str
    paragraphs: List[str] = field(default_factory=list)
    bullets: List[str] = field(default_factory=list)
    tables: List[TableData] = field(default_factory=list)
    figures: List[Figure] = field(default_factory=list)

@dataclass
class DocumentMeta:
    title: str
    subtitle: Optional[str] = None
    author: Optional[str] = None
    date: Optional[str] = None  # "YYYY-MM-DD"

@dataclass
class DocumentModel:
    meta: DocumentMeta
    sections: List[Section] = field(default_factory=list)
    references: List[str] = field(default_factory=list)  # 直接放超链接或“[1] xxx”

# -------- helpers --------
def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def _parse_md_to_sections(md_text: str) -> List[Section]:
    """非常轻量的 Markdown → Section 解析：按 `##` 拆段，普通行做段落，`- ` 做 bullets。"""
    sections: List[Section] = []
    cur: Optional[Section] = None
    for line in (md_text or "").splitlines():
        if re.match(r"^#{2,3}\s+", line):
            title = re.sub(r"^#{2,3}\s+", "", line).strip()
            cur = Section(title=title); sections.append(cur); continue
        if cur is None:
            # 若开头没有 ##，给一个默认段
            cur = Section(title="正文"); sections.append(cur)
        s = line.strip()
        if not s:
            continue
        if s.startswith("- "):
            cur.bullets.append(s[2:].strip())
        else:
            cur.paragraphs.append(s)
    return sections

def build_model_from_inputs(
    *, state_path: Optional[str] = None,
    draft_path: Optional[str] = None,
    free_text: Optional[str] = None,
    meta_overrides: Optional[Dict[str, str]] = None,
) -> DocumentModel:
    """把 QueryEngine/ReportEngine 产物装配成结构化 DocumentModel。"""
    meta_overrides = meta_overrides or {}
    title = meta_overrides.get("title") or "研究报告"
    subtitle = meta_overrides.get("subtitle")
    author = meta_overrides.get("author") or "Auto Researcher"
    date = meta_overrides.get("date") or _today()

    sections: List[Section] = []
    refs: List[str] = []

    # 1) state.json（若有的话）
    state: Dict[str, Any] = {}
    if state_path and Path(state_path).exists():
        try:
            state = json.loads(Path(state_path).read_text(encoding="utf-8"))
        except Exception:
            state = {}

        # 常见字段名兜底（你的节点里通常会沉淀结构/参考文献）
        for key in ("references", "refs", "citations"):
            if isinstance(state.get(key), list):
                refs = [str(x) for x in state[key]]
                break

        # 如果 state 已有结构化的 sections，就直接用
        if isinstance(state.get("sections"), list) and state["sections"]:
            for s in state["sections"]:
                sec = Section(
                    title=str(s.get("title") or s.get("heading") or "未命名段落"),
                    paragraphs=list(s.get("paragraphs") or []),
                    bullets=list(s.get("bullets") or []),
                )
                sections.append(sec)

    # 2) draft.md（若有就增量解析补齐）
    md_text = ""
    if draft_path and Path(draft_path).exists():
        try:
            md_text = Path(draft_path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            md_text = ""
    elif free_text:
        md_text = free_text

    if md_text:
        md_sections = _parse_md_to_sections(md_text)
        # 若 state 里本就有 sections，就尝试按标题 merge，否则直接采用解析结果
        if sections:
            known_titles = {s.title for s in sections}
            for ms in md_sections:
                if ms.title in known_titles:
                    # 附加内容
                    for s in sections:
                        if s.title == ms.title:
                            s.paragraphs += ms.paragraphs
                            s.bullets += ms.bullets
                else:
                    sections.append(ms)
        else:
            sections = md_sections

    if not sections:
        sections = [Section(title="正文", paragraphs=[free_text or "（无可用正文）"])]

    model = DocumentModel(
        meta=DocumentMeta(title=title, subtitle=subtitle, author=author, date=date),
        sections=sections,
        references=refs,
    )
    return model
