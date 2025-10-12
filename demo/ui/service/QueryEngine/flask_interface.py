# -*- coding: utf-8 -*-
"""
QueryEngine FastAPI 子路由（与 ReportEngine 同风格）
- 路由前缀: /api/query
- 异步/同步两用：run_query_sync(...) 可被主控 /api/chat 编排直接调用
- 任务队列：/run -> /progress/{id} -> /result/{id}
- 工具直连：/tools（列举）  /tool（执行指定 Tavily 工具）
"""

from __future__ import annotations
import os
import json
import time
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, Response

# 你的 QueryEngine 代码
from .agent import DeepSearchAgent
from .utils.config import load_config

query_router = APIRouter(prefix="/api/query", tags=["query"])

_QUERY_AGENT: Optional[DeepSearchAgent] = None
_INITIALIZED: bool = False
_LAST_ERROR: Optional[str] = None

_TASKS: Dict[str, "QueryTask"] = {}
_TASK_LOCK = threading.Lock()

# ---------------- 任务结构体 ----------------
@dataclass
class QueryTask:
    task_id: str
    query: str
    status: str = "pending"        # pending, running, completed, error, cancelled
    progress: int = 0
    error_message: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    report_md: str = ""            # 最终 Markdown 文本
    output_path: str = ""          # 最终报告（若保存）.md
    draft_path: str = ""           # 初稿 draft_*.md
    state_path: str = ""           # 状态 state_*.json

    def update(self, *, status: Optional[str] = None,
                     progress: Optional[int] = None,
                     error: Optional[str] = None):
        if status is not None:
            self.status = status
        if progress is not None:
            self.progress = max(0, min(100, progress))
        if error:
            self.error_message = error
        self.updated_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "query": self.query,
            "status": self.status,
            "progress": self.progress,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "has_result": bool(self.report_md),
            "output_path": self.output_path,
            "draft_path": self.draft_path,
            "state_path": self.state_path,
        }

# ------------- 初始化（幂等） -------------
def _resolve_output_dir_fallback(cfg_output_dir: Optional[str]) -> Path:
    # 优先：主控工具函数
    try:
        from service.utils.path_utils import get_query_dir  # 主控已把仓根进了sys.path
        return Path(get_query_dir()).resolve()
    except Exception:
        pass
    # 次选：环境变量
    env_dir = os.getenv("QUERY_OUTPUT_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    # 兜底：固定默认子目录
    base = Path(cfg_output_dir or "./reports/query_engine_streamlit_reports")
    return base.resolve()

def initialize_query_engine() -> bool:
    """
    幂等初始化 DeepSearchAgent
    """
    global _QUERY_AGENT, _INITIALIZED, _LAST_ERROR
    if _INITIALIZED and _QUERY_AGENT is not None:
        return True
    try:
        cfg = load_config()
        # 强制对齐输出目录
        out_dir = _resolve_output_dir_fallback(getattr(cfg, "output_dir", None))
        cfg.output_dir = str(out_dir)

        _QUERY_AGENT = DeepSearchAgent(cfg)
        _INITIALIZED = True
        _LAST_ERROR = None
        print("[QueryEngine] initialize -> True")
        print(f"[QueryEngine] output_dir = {cfg.output_dir}")
        return True
    except Exception as e:
        _QUERY_AGENT = None
        _INITIALIZED = False
        _LAST_ERROR = f"{type(e).__name__}: {e}"
        print(f"[QueryEngine] 初始化失败: {_LAST_ERROR}")
        print("[QueryEngine] initialize -> False")
        return False

# 模块导入即尝试一次（失败也不抛）
initialize_query_engine()

# ------------- 工具方法 -------------
def _list_files(output_dir: Path, pattern: str) -> List[Path]:
    return sorted(
        output_dir.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

def _diff_new_files(before: List[Path], after: List[Path]) -> List[Path]:
    bs = {p.name for p in before}
    return [p for p in after if p.name not in bs]

def _safe_read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""

# ------------- 同步调用（给 main.py 的 /api/chat 用） -------------
async def run_query_sync(query: str, *, save_report: bool = True, timeout_s: float = 300.0) -> Dict[str, Any]:
    """
    主控 /api/chat 在识别到“应进行深度搜索/研究”时可直接调用。
    返回：
      成功: {"ok": True, "result": {"length": int, "output_path": "...", "draft_path": "...", "state_path":"..."}}
      失败: {"ok": False, "error": "..."}
    """
    try:
        if not initialize_query_engine() or _QUERY_AGENT is None:
            return {"ok": False, "error": _LAST_ERROR or "QueryEngine not initialized"}

        out_dir = Path(_QUERY_AGENT.config.output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        # 记录前置文件集
        b_deep  = _list_files(out_dir, "deep_search_report_*.md")
        b_draft = _list_files(out_dir, "draft_*.md")
        b_state = _list_files(out_dir, "state_*.json")

        # 执行深度研究
        md = _QUERY_AGENT.research(query, save_report=save_report)

        # 采集新增
        a_deep  = _list_files(out_dir, "deep_search_report_*.md")
        a_draft = _list_files(out_dir, "draft_*.md")
        a_state = _list_files(out_dir, "state_*.json")

        new_deep  = _diff_new_files(b_deep,  a_deep)
        new_draft = _diff_new_files(b_draft, a_draft)
        new_state = _diff_new_files(b_state, a_state)

        output_path = str((new_deep[0] if new_deep else (a_deep[0] if a_deep else Path()))) if (a_deep or new_deep) else ""
        draft_path  = str((new_draft[0] if new_draft else (a_draft[0] if a_draft else Path()))) if (a_draft or new_draft) else ""
        state_path  = str((new_state[0] if new_state else (a_state[0] if a_state else Path()))) if (a_state or new_state) else ""

        return {
            "ok": True,
            "result": {
                "length": len(md or ""),
                "output_path": output_path,
                "draft_path": draft_path,
                "state_path": state_path
            }
        }
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

# ------------- 后台任务执行逻辑（线程） -------------
def _run_task_thread(task_id: str):
    task = _TASKS.get(task_id)
    if not task:
        return
    try:
        task.update(status="running", progress=10)

        ok = initialize_query_engine()
        if not ok or _QUERY_AGENT is None:
            task.update(status="error", progress=0, error=_LAST_ERROR or "QueryEngine not initialized")
            return

        out_dir = Path(_QUERY_AGENT.config.output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        # 30%：预备
        task.update(progress=30)
        b_deep  = _list_files(out_dir, "deep_search_report_*.md")
        b_draft = _list_files(out_dir, "draft_*.md")
        b_state = _list_files(out_dir, "state_*.json")

        # 80%：执行研究
        task.update(progress=80)
        md = _QUERY_AGENT.research(task.query, save_report=True)
        task.report_md = md or ""

        # 90%：捕获输出文件
        task.update(progress=90)
        a_deep  = _list_files(out_dir, "deep_search_report_*.md")
        a_draft = _list_files(out_dir, "draft_*.md")
        a_state = _list_files(out_dir, "state_*.json")

        new_deep  = _diff_new_files(b_deep,  a_deep)
        new_draft = _diff_new_files(b_draft, a_draft)
        new_state = _diff_new_files(b_state, a_state)

        if new_deep:  task.output_path = str(new_deep[0])
        elif a_deep:  task.output_path = str(a_deep[0])

        if new_draft: task.draft_path  = str(new_draft[0])
        elif a_draft: task.draft_path  = str(a_draft[0])

        if new_state: task.state_path  = str(new_state[0])
        elif a_state: task.state_path  = str(a_state[0])

        # 100%：完成
        task.update(status="completed", progress=100)
    except Exception as e:
        task.update(status="error", progress=0, error=str(e))

# ------------- REST API -------------
@query_router.get("/status")
def get_status():
    try:
        init_ok = initialize_query_engine()
        info = {
            "success": True,
            "initialized": init_ok and (_QUERY_AGENT is not None),
            "error": _LAST_ERROR,
            "tasks": len(_TASKS),
        }
        if _QUERY_AGENT is not None:
            cfg = _QUERY_AGENT.config
            info.update({
                "output_dir": cfg.output_dir,
                "model": getattr(getattr(_QUERY_AGENT, "llm_client", None), "get_model_info", lambda: "unknown")(),
                "tavily_enabled": bool(getattr(cfg, "tavily_api_key", "")),
            })
        return JSONResponse(info)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@query_router.post("/run")
def run_query(payload: Dict[str, Any] = Body(...)):
    """
    启动异步深度研究任务（后台线程）
    body: {"query": "..."}
    """
    try:
        query = (payload.get("query") or "").strip()
        if not query:
            return JSONResponse({"success": False, "error": "query 不能为空"}, status_code=400)

        with _TASK_LOCK:
            task_id = f"query_{int(time.time()*1000)}"
            task = QueryTask(task_id=task_id, query=query)
            _TASKS[task_id] = task

        t = threading.Thread(target=_run_task_thread, args=(task_id,), daemon=True)
        t.start()

        return JSONResponse({"success": True, "task": task.to_dict()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@query_router.get("/progress/{task_id}")
def get_progress(task_id: str):
    try:
        task = _TASKS.get(task_id)
        if not task:
            # 与 ReportEngine 一致：任务不存在也返回 completed，避免前端死等
            return JSONResponse({"success": True, "task": {
                "task_id": task_id, "status": "completed", "progress": 100,
                "error_message": "", "has_result": False
            }})
        return JSONResponse({"success": True, "task": task.to_dict()})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@query_router.get("/result/{task_id}")
def get_result(task_id: str):
    try:
        task = _TASKS.get(task_id)
        if not task:
            return JSONResponse({"success": False, "error": "任务不存在"}, status_code=404)
        if task.status != "completed":
            return JSONResponse({"success": False, "error": "任务尚未完成", "task": task.to_dict()}, status_code=400)
        # DeepSearchAgent 产物为 Markdown
        return Response(task.report_md, media_type="text/markdown")
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@query_router.get("/result/{task_id}/json")
def get_result_json(task_id: str):
    try:
        task = _TASKS.get(task_id)
        if not task:
            return JSONResponse({"success": False, "error": "任务不存在"}, status_code=404)
        if task.status != "completed":
            return JSONResponse({"success": False, "error": "任务尚未完成", "task": task.to_dict()}, status_code=400)
        return JSONResponse({"success": True, "task": task.to_dict(), "report_md": task.report_md})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@query_router.post("/cancel/{task_id}")
def cancel_task(task_id: str):
    try:
        with _TASK_LOCK:
            task = _TASKS.get(task_id)
            if not task:
                return JSONResponse({"success": False, "error": "任务不存在"}, status_code=404)
            if task.status == "running":
                task.update(status="cancelled", progress=0, error="用户取消任务")
            _TASKS.pop(task_id, None)
        return JSONResponse({"success": True, "message": "任务已取消"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# ---------------- 工具直连（保留 Tavily 全部能力） ----------------
_SUPPORTED_TOOLS: List[Tuple[str, str]] = [
    ("basic_search_news", "基础新闻搜索（快速通用，可设 max_results）"),
    ("deep_search_news", "深度新闻分析"),
    ("search_news_last_24_hours", "最近24小时新闻"),
    ("search_news_last_week", "最近一周新闻"),
    ("search_images_for_news", "新闻相关图片搜索"),
    ("search_news_by_date", "按日期范围搜索（需 start_date/end_date: YYYY-MM-DD）"),
]

@query_router.get("/tools")
def list_tools():
    return JSONResponse({"success": True, "tools": [
        {"name": n, "desc": d} for (n, d) in _SUPPORTED_TOOLS
    ]})

@query_router.post("/tool")
def run_tool(payload: Dict[str, Any] = Body(...)):
    """
    直接调用某个搜索工具（便于最小闭环验证工具链）
    """
    try:
        if not initialize_query_engine() or _QUERY_AGENT is None:
            return JSONResponse({"success": False, "error": _LAST_ERROR or "QueryEngine not initialized"}, status_code=500)

        tool = (payload.get("tool") or "").strip()
        query = (payload.get("query") or "").strip()
        if not tool or not query:
            return JSONResponse({"success": False, "error": "tool 与 query 不能为空"}, status_code=400)

        kwargs = {k: v for k, v in payload.items() if k not in ("tool", "query")}
        resp = _QUERY_AGENT.execute_search_tool(tool, query, **kwargs)

        results_out = []
        if resp and getattr(resp, "results", None):
            for r in resp.results:
                results_out.append({
                    "title": getattr(r, "title", None),
                    "url": getattr(r, "url", None),
                    "content": getattr(r, "content", None),
                    "raw_content": getattr(r, "raw_content", None),
                    "score": getattr(r, "score", None),
                    "published_date": getattr(r, "published_date", None),
                })
        return JSONResponse({"success": True, "count": len(results_out), "results": results_out})
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"success": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)
