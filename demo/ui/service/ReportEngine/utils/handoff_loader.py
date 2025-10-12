# -*- coding: utf-8 -*-
from __future__ import annotations
import json, os, glob
from pathlib import Path
from typing import Optional, Tuple

def _latest(pattern: str) -> Optional[str]:
    files = glob.glob(pattern)
    if not files:
        return None
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[0]

def load_handoff_or_fallback(query_dir: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    返回 (deep_report_path, draft_path, state_path)，找不到则为 None
    优先 handoff_*.json；否则找各自最新的文件。
    """
    Path(query_dir).mkdir(parents=True, exist_ok=True)

    # 1) handoff 优先
    h = _latest(os.path.join(query_dir, "handoff_*.json"))
    if h and os.path.exists(h):
        try:
            data = json.loads(open(h, "r", encoding="utf-8").read())
            p = data.get("paths") or {}
            return p.get("deep_report"), p.get("draft"), p.get("state")
        except Exception:
            pass

    # 2) 无 handoff：各自取最新
    deep_report = _latest(os.path.join(query_dir, "deep_search_report_*.md"))
    draft = _latest(os.path.join(query_dir, "draft_*.md"))
    state = _latest(os.path.join(query_dir, "state_*.json"))
    return deep_report, draft, state
