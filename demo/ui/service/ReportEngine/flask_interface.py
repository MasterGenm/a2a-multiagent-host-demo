# -*- coding: utf-8 -*-
"""
ReportEngine FastAPI 子路由（增强版 + 幂等初始化）
- 兼容原 Flask 蓝图能力：/status /generate /progress/{id} /result/{id} /cancel/{id}
- 保留 run_report_sync(...) 供主控 /api/chat 同步委托
- 新增：run_report_sync 支持“文件模式（files）”，并标准化返回字段
"""

from __future__ import annotations
import os
import time
import json
import logging
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Union

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, Response

from .agent import ReportAgent
from .utils.config import load_config

report_router = APIRouter(prefix="/api/report", tags=["report"])

_REPORT_AGENT: Optional[ReportAgent] = None
_INITIALIZED: bool = False
_LAST_ERROR: Optional[str] = None
_INIT_LOCK = threading.Lock()

_TASKS: Dict[str, "ReportTask"] = {}
_TASK_LOCK = threading.Lock()

_LOG = logging.getLogger("ReportEngine")


# -------------------- 工具函数 --------------------
def _normpath(p: Optional[str]) -> str:
    """把路径标准化为绝对路径，兼容 Windows"""
    try:
        if not p:
            return ""
        return os.path.normpath(str(Path(p).resolve()))
    except Exception:
        return p or ""


def _safe_get_report_title(agent: ReportAgent) -> str:
    """尽量从 Agent 拿到最后一次标题（如果没有也不要抛错）"""
    for attr in ("last_report_title", "report_title", "last_title"):
        if hasattr(agent, attr):
            try:
                v = getattr(agent, attr)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            except Exception:
                pass
    return ""


# -------------------- 任务模型 --------------------
@dataclass
class ReportTask:
    task_id: str
    query: str
    custom_template: str = ""
    status: str = "pending"         # pending, running, completed, error, cancelled
    progress: int = 0
    error_message: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    html_content: str = ""
    html_path: str = ""             # 新增：最终 HTML 文件路径（标准化）

    def update(self, *, status: Optional[str] = None,
                     progress: Optional[int] = None,
                     error: Optional[str] = None) -> None:
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
            "custom_template": self.custom_template,
            "status": self.status,
            "progress": self.progress,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "has_result": bool(self.html_content or self.html_path),
            "html_path": self.html_path,
        }


_STARTUP_PRINTED = False


# -------------------- 初始化 --------------------
def initialize_report_engine() -> bool:
    global _REPORT_AGENT, _INITIALIZED, _LAST_ERROR, _STARTUP_PRINTED
    if _INITIALIZED and _REPORT_AGENT is not None:
        return True
    with _INIT_LOCK:
        if _INITIALIZED and _REPORT_AGENT is not None:
            return True
        try:
            cfg = load_config()
            _REPORT_AGENT = ReportAgent(cfg)
            _INITIALIZED = True
            _LAST_ERROR = None
            if not _STARTUP_PRINTED:
                print("[ReportEngine] initialize -> True")
                _STARTUP_PRINTED = True
            return True
        except Exception as e:
            _REPORT_AGENT = None
            _INITIALIZED = False
            _LAST_ERROR = f"{type(e).__name__}: {e}"
            print(f"[ReportEngine] 初始化失败: {_LAST_ERROR}")
            print("[ReportEngine] initialize -> False")
            return False


@report_router.on_event("startup")
async def _startup_init_report_engine():
    initialize_report_engine()


# -------------------- 同步入口：支持 prompt/文件 两种模式 --------------------
async def run_report_sync(
    query: Union[str, Dict[str, Any]],
    *,
    timeout_s: float = 180.0,
    custom_template: str = "",
) -> Dict[str, Any]:
    """
    用法1（原有）：await run_report_sync("请基于研究材料生成HTML报告", timeout_s=180)
    用法2（新增文件模式）：
        await run_report_sync({
            "mode": "files",
            "query": "（可选）报告标题/主题",
            "draft_path": "reports/draft_xxx.md",
            "state_path": "reports/state_xxx.json",
            "forum_path": "logs/forum.log",
            "custom_template": "",      # 可选
            "save_html": True           # 可选，默认 True
        })
    兼容别名：query_engine_draft/query_engine_state/forum_log_path
    返回统一结构：
        {"ok": True, "result": {
            "html_len": N,
            "html_path": "/abs/path/to/final.html",
            "custom_template": "xxx.md",
            "report_title": "（尽力从 Agent 获取）"
        }}
    """
    try:
        if not initialize_report_engine() or _REPORT_AGENT is None:
            return {"ok": False, "error": _LAST_ERROR or "ReportEngine not initialized"}

        # --------- 模式自动判定 ---------
        if isinstance(query, dict):
            payload = dict(query)
            mode = (payload.get("mode") or "").lower().strip()
            # 若未显式给 mode，但提供了 draft/state 路径，也按 files 处理
            if (not mode) and (payload.get("draft_path") or payload.get("query_engine_draft")
                               or payload.get("state_path") or payload.get("query_engine_state")):
                mode = "files"

            if mode == "files":
                qtext = payload.get("query")  # 可为空，让 Agent 自行从 state 推断
                draft_path = payload.get("draft_path") or payload.get("query_engine_draft")
                state_path = payload.get("state_path") or payload.get("query_engine_state")
                forum_path = payload.get("forum_path") or payload.get("forum_log_path")
                ctpl = payload.get("custom_template") or custom_template
                save_html = payload.get("save_html")
                if save_html is None:
                    save_html = True

                try:
                    html_path = _REPORT_AGENT.generate_report_from_files(
                        query=qtext,
                        query_dir=None,
                        draft_path=draft_path,
                        state_path=state_path,
                        forum_log_path=forum_path,
                        custom_template=ctpl,
                        save_report=bool(save_html),
                    )
                    if not html_path:
                        html_path = _REPORT_AGENT.get_last_saved_html_path()
                except Exception as e:
                    traceback.print_exc()
                    return {"ok": False, "error": f"{type(e).__name__}: {e}"}

                html_path = _normpath(html_path)
                html_len = 0
                if html_path and Path(html_path).exists():
                    try:
                        html_len = len(Path(html_path).read_text(encoding="utf-8", errors="ignore"))
                    except Exception:
                        pass

                return {"ok": True, "result": {
                    "html_len": html_len,
                    "html_path": html_path,
                    "custom_template": ctpl or "",
                    "report_title": _safe_get_report_title(_REPORT_AGENT),
                }}

            # 非 files，则回落到老的“prompt+自动搜素材”模式
            query_text = (payload.get("query") or "").strip() or "综合报告"
            custom_template = payload.get("custom_template") or custom_template
        else:
            # 纯字符串模式
            query_text = (query or "").strip() or "综合报告"

        # --------- 旧模式：prompt + 自动加载上游产物 ---------
        cfg = _REPORT_AGENT.config

        # 推断 forum 日志（若没有，就用 log_file 兜底）
        forum_log_path = str(Path(cfg.log_file).with_name("forum.log"))
        if not Path(forum_log_path).exists():
            forum_log_path = cfg.log_file

        # 尝试读取上游产物；缺失也允许最小输入生成
        try:
            status = _REPORT_AGENT.check_input_files(
                cfg.insight_dir, cfg.media_dir, cfg.query_dir, forum_log_path
            )
            if status and status.get("ready"):
                content = _REPORT_AGENT.load_input_files(status.get("latest_files", {}))
            else:
                content = {"reports": [], "forum_logs": ""}
        except Exception:
            content = {"reports": [], "forum_logs": ""}

        html = _REPORT_AGENT.generate_report(
            query=query_text,
            reports=content.get("reports", []),
            forum_logs=content.get("forum_logs", ""),
            custom_template=custom_template,
            save_report=True,
        )
        html_path = ""
        try:
            html_path = _REPORT_AGENT.get_last_saved_html_path()
        except Exception:
            pass

        html_path = _normpath(html_path)
        return {"ok": True, "result": {
            "html_len": len(html or ""),
            "html_path": html_path,
            "custom_template": custom_template or "",
            "report_title": _safe_get_report_title(_REPORT_AGENT),
        }}
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# -------------------- 异步任务线程（保持原有逻辑 + 记录路径） --------------------
def _run_task_thread(task_id: str) -> None:
    task = _TASKS.get(task_id)
    if not task:
        return
    try:
        task.update(status="running", progress=10)

        ok = initialize_report_engine()
        if not ok or _REPORT_AGENT is None:
            task.update(status="error", progress=0, error=_LAST_ERROR or "ReportEngine not initialized")
            return

        cfg = _REPORT_AGENT.config
        forum_log_path = str(Path(cfg.log_file).with_name("forum.log"))
        if not Path(forum_log_path).exists():
            forum_log_path = cfg.log_file

        task.update(progress=30)
        status = _REPORT_AGENT.check_input_files(cfg.insight_dir, cfg.media_dir, cfg.query_dir, forum_log_path)

        task.update(progress=50)
        if status and status.get("ready"):
            content = _REPORT_AGENT.load_input_files(status.get("latest_files", {}))
        else:
            content = {"reports": [], "forum_logs": ""}

        task.update(progress=90)
        html = _REPORT_AGENT.generate_report(
            query=task.query,
            reports=content.get("reports", []),
            forum_logs=content.get("forum_logs", ""),
            custom_template=task.custom_template,
            save_report=True,
        )
        task.html_content = html
        try:
            task.html_path = _normpath(_REPORT_AGENT.get_last_saved_html_path())
        except Exception:
            task.html_path = task.html_path or ""
        task.update(status="completed", progress=100)
    except Exception as e:
        task.update(status="error", progress=0, error=str(e))


# -------------------- REST 路由（保持原有 + 小增强） --------------------
@report_router.get("/status")
def get_status():
    try:
        init_ok = initialize_report_engine()
        info = {
            "success": True,
            "initialized": init_ok and (_REPORT_AGENT is not None),
            "error": _LAST_ERROR,
            "tasks": len(_TASKS),
        }
        if _REPORT_AGENT is not None:
            cfg = _REPORT_AGENT.config
            try:
                model_info = _REPORT_AGENT.llm_client.get_model_info()  # type: ignore
            except Exception:
                model_info = "unknown"
            last_html = ""
            try:
                last_html = _normpath(_REPORT_AGENT.get_last_saved_html_path())
            except Exception:
                pass
            info.update({
                "output_dir": _normpath(cfg.output_dir),
                "template_dir": _normpath(cfg.template_dir),
                "log_file": _normpath(cfg.log_file),
                "model": model_info,
                "last_html_path": last_html,
            })
        return JSONResponse(info, status_code=200)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=200)


@report_router.post("/generate")
def generate_report(payload: Dict[str, Any] = Body(...)):
    """
    异步任务接口（保持原有）：暂未接入 files 模式，前端需要 files 直出请用 run_report_sync 的 dict 调用，
    或另开同步接口（按需增加）。
    """
    try:
        query = (payload.get("query") or "").strip() or "综合报告"
        custom_template = payload.get("custom_template") or ""

        with _TASK_LOCK:
            task_id = f"report_{int(time.time() * 1000)}"
            task = ReportTask(task_id=task_id, query=query, custom_template=custom_template)
            _TASKS[task_id] = task

        t = threading.Thread(target=_run_task_thread, args=(task_id,), daemon=True)
        t.start()

        return JSONResponse({"success": True, "task": task.to_dict()}, status_code=200)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=200)


@report_router.get("/progress/{task_id}")
def get_progress(task_id: str):
    try:
        task = _TASKS.get(task_id)
        if not task:
            # 兼容老逻辑：任务不存在也返回 completed（避免前端死等）
            return JSONResponse({"success": True, "task": {
                "task_id": task_id, "status": "completed", "progress": 100,
                "error_message": "", "has_result": True, "html_path": ""}}, status_code=200)
        return JSONResponse({"success": True, "task": task.to_dict()}, status_code=200)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=200)


@report_router.get("/result/{task_id}")
def get_result(task_id: str):
    try:
        task = _TASKS.get(task_id)
        if not task:
            return JSONResponse({"success": False, "error": "任务不存在"}, status_code=200)
        if task.status != "completed":
            return JSONResponse({"success": False, "error": "报告尚未完成", "task": task.to_dict()}, status_code=200)
        # 若需要前端直接访问文件，也可以读取 task.html_path；这里保持返回 HTML 内容
        return Response(task.html_content, media_type="text/html")
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=200)


@report_router.get("/result/{task_id}/json")
def get_result_json(task_id: str):
    try:
        task = _TASKS.get(task_id)
        if not task:
            return JSONResponse({"success": False, "error": "任务不存在"}, status_code=200)
        if task.status != "completed":
            return JSONResponse({"success": False, "error": "报告尚未完成", "task": task.to_dict()}, status_code=200)
        return JSONResponse({"success": True, "task": task.to_dict(), "html_content": task.html_content}, status_code=200)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=200)


@report_router.post("/cancel/{task_id}")
def cancel_task(task_id: str):
    try:
        with _TASK_LOCK:
            task = _TASKS.get(task_id)
            if not task:
                return JSONResponse({"success": False, "error": "任务不存在"}, status_code=200)
            if task.status == "running":
                task.update(status="cancelled", progress=0, error="用户取消任务")
            _TASKS.pop(task_id, None)
        return JSONResponse({"success": True, "message": "任务已取消"}, status_code=200)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=200)


# -------------------- 简易模板与日志接口（保留） --------------------
def _read_lines_safe(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return [ln.rstrip("\r\n") for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception:
        return []


@report_router.get("/templates")
def get_templates():
    try:
        if not initialize_report_engine() or _REPORT_AGENT is None:
            return JSONResponse({"success": False, "error": _LAST_ERROR or "ReportEngine not initialized"}, status_code=200)
        tpl_dir = Path(_REPORT_AGENT.config.template_dir)
        items = []
        if tpl_dir.exists():
            for p in sorted(tpl_dir.glob("*.md")):
                try:
                    content = p.read_text(encoding="utf-8")
                    items.append({"name": p.stem, "filename": p.name,
                                  "description": (content.splitlines()[0] if content else "无描述"),
                                  "size": len(content)})
                except Exception:
                    pass
        return JSONResponse({"success": True, "templates": items, "template_dir": _normpath(str(tpl_dir))}, status_code=200)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=200)


@report_router.get("/log")
def get_log():
    try:
        if not initialize_report_engine() or _REPORT_AGENT is None:
            return JSONResponse({"success": False, "error": _LAST_ERROR or "ReportEngine not initialized"}, status_code=200)
        log_path = Path(_REPORT_AGENT.config.log_file)
        return JSONResponse({"success": True, "log_lines": _read_lines_safe(log_path)}, status_code=200)
    except Exception as e:
        return JSONResponse({"success": False, "error": f"读取日志失败: {e}"}, status_code=200)


@report_router.post("/log/clear")
def clear_log():
    try:
        if not initialize_report_engine() or _REPORT_AGENT is None:
            return JSONResponse({"success": False, "error": _LAST_ERROR or "ReportEngine not initialized"}, status_code=200)
        log_path = Path(_REPORT_AGENT.config.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        return JSONResponse({"success": True, "message": "日志已清空"}, status_code=200)
    except Exception as e:
        return JSONResponse({"success": False, "error": f"清空日志失败: {e}"}, status_code=200)
