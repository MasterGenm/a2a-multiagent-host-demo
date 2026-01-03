# -*- coding: utf-8 -*-
"""
ReportEngine FastAPI 子路由（增强版 + 幂等初始化 + 原生 DOCX/PDF 直出）
- 兼容原 Flask 蓝图能力：/status /generate /progress/{id} /result/{id} /cancel/{id}
- 保留 run_report_sync(...) 供主控 /api/chat 同步委托
- 新增：
  1) run_report_sync 支持“文件模式（files）”
  2) 新增 output_format = html|docx|pdf（默认 html，保持兼容）
  3) 当 output_format 为 docx/pdf 时，直接走 python-docx / reportlab，不经由 HTML 中转

用法示例（与主控 main.py 配合）：
await run_report_sync({
    "mode": "files",
    "query": "国内人工智能进展季度盘点",
    "draft_path": "E:/.../query_outputs/draft_2025-10-28.md",
    "state_path": "E:/.../query_outputs/state_2025-10-28.json",
    "forum_path": "logs/forum.log",
    "custom_template": "金融科技技术与应用发展.md",
    "output_format": "docx"   # 或 "pdf" / "html"
})
"""

from __future__ import annotations

import os
import time
import json
import logging
import threading
import traceback

# --- ADD IMPORTS (如果你文件顶部没这些，就补上) ---
import mimetypes
from pathlib import Path

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse


from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Union, List

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, Response

from .agent import ReportAgent
from .utils.config import load_config

# —— 新增：结构化模型 & Writer 适配层 —— #
try:
    from .model import build_model_from_inputs
    from .writers.base import pick_writer, pick_ext
    _WRITER_AVAILABLE = True
except Exception as _e:
    # 若你尚未按方案添加 model.py / writers/，仍可使用 HTML 产线
    _WRITER_AVAILABLE = False
    _WRITER_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"

report_router = APIRouter(prefix="/api/report", tags=["report"])

_REPORT_AGENT: Optional[ReportAgent] = None
_INITIALIZED: bool = False
_LAST_ERROR: Optional[str] = None
_INIT_LOCK = threading.Lock()

_TASKS: Dict[str, "ReportTask"] = {}
_TASK_LOCK = threading.Lock()

_LOG = logging.getLogger("ReportEngine")

_STARTUP_PRINTED = False


# -------------------- 工具函数 --------------------
# --- ADD: download helpers + endpoint ---



# 以本文件位置推一个稳定的 ui/reports 根目录
_UI_ROOT = Path(__file__).resolve().parents[2]          # .../demo/ui
_DEFAULT_REPORTS_ROOT = (_UI_ROOT / "reports").resolve()

def _is_under_any_root(p: Path, roots: List[Path]) -> bool:
    rp = p.resolve()
    for r in roots:
        rr = r.resolve()
        try:
            if rp.is_relative_to(rr):  # py3.9+
                return True
        except Exception:
            try:
                rp.relative_to(rr)
                return True
            except Exception:
                pass
    return False

@report_router.get("/download")
def download_report(
    path: str = Query(..., description="Absolute path or relative path"),
    format: str = Query("auto", description="auto|html|pdf|docx|md"),
    inline: bool = Query(False, description="open inline if true"),
):
    fmt = (format or "auto").lower().strip()
    allowed_fmt = {"auto", "html", "pdf", "docx", "md"}
    if fmt not in allowed_fmt:
        raise HTTPException(status_code=422, detail=f"invalid format: {format}")

    # 解析路径
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    else:
        p = p.resolve()

    # 收集允许目录（白名单）
    roots: List[Path] = [_DEFAULT_REPORTS_ROOT]

    # 尽量从 ReportEngine 配置拿 output_dir（不依赖 get_report_agent）
    try:
        if not initialize_report_engine() or _REPORT_AGENT is None:
            raise RuntimeError("ReportEngine not initialized")
        cfg_out = getattr(_REPORT_AGENT.config, "output_dir", None)
        if cfg_out:
            roots.append(Path(cfg_out).expanduser().resolve())
    except Exception:
        pass

    roots.append((Path.cwd() / "outputs" / "final").resolve())
    env_final = os.getenv("REPORT_FINAL_DIR")
    if env_final:
        roots.append(Path(env_final).expanduser().resolve())

    if not _is_under_any_root(p, roots):
        raise HTTPException(status_code=403, detail="forbidden path")

    ext_map = {"html": ".html", "pdf": ".pdf", "docx": ".docx", "md": ".md"}

    target = p
    if fmt != "auto":
        want_ext = ext_map[fmt]
        if p.suffix.lower() != want_ext:
            target = p.with_suffix(want_ext)

        # fallback：pdf/docx 不存在时退回原文件，避免旧 UI 404
        if (not target.exists()) and p.exists():
            target = p

    if (not target.exists()) or (not target.is_file()):
        raise HTTPException(status_code=404, detail="file not found")

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"

    if inline:
        headers = {"Content-Disposition": f'inline; filename="{target.name}"'}
        return FileResponse(str(target), media_type=media_type, headers=headers)

    return FileResponse(str(target), media_type=media_type, filename=target.name)



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


def _ensure_dir(p: str) -> None:
    Path(p).mkdir(parents=True, exist_ok=True)


def _final_output_dir(cfg) -> str:
    """
    决定 DOCX/PDF 的落盘目录：
      1) 环境变量 REPORT_FINAL_DIR
      2) cfg.output_dir（ReportAgent 配置里的输出目录）
      3) 默认 'outputs/final'
    """
    cand = os.getenv("REPORT_FINAL_DIR")
    if cand:
        return _normpath(cand)
    if getattr(cfg, "output_dir", None):
        return _normpath(cfg.output_dir)
    return _normpath(str(Path("outputs/final").resolve()))


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


# -------------------- 同步入口：支持 prompt/文件 + html/docx/pdf --------------------
async def run_report_sync(
    query: Union[str, Dict[str, Any]],
    *,
    timeout_s: float = 180.0,
    custom_template: str = "",
) -> Dict[str, Any]:
    """
    用法1（原有）：await run_report_sync("请基于研究材料生成HTML报告", timeout_s=180)
    用法2（文件模式）：
        await run_report_sync({
            "mode": "files",
            "query": "（可选标题/主题）",
            "draft_path": "reports/draft_xxx.md",
            "state_path": "reports/state_xxx.json",
            "forum_path": "logs/forum.log",
            "custom_template": "",
            "save_html": True,
            "output_format": "docx" | "pdf" | "html"
        })

    返回：
      - html: {"ok": True, "result": {"html_len", "html_path", "custom_template", "report_title"}}
      - docx: {"ok": True, "result": {"docx_len", "docx_path", "report_title"}}
      - pdf : {"ok": True, "result": {"pdf_len",  "pdf_path",  "report_title"}}
    """
    try:
        if not initialize_report_engine() or _REPORT_AGENT is None:
            return {"ok": False, "error": _LAST_ERROR or "ReportEngine not initialized"}

        # 读取输出格式（默认 html 保持兼容）
        def _resolve_output_format(q) -> str:
            if isinstance(q, dict):
                v = (q.get("output_format") or os.getenv("REPORTENGINE_OUTPUT") or "html").lower().strip()
            else:
                v = (os.getenv("REPORTENGINE_OUTPUT") or "html").lower().strip()
            return v if v in ("html", "docx", "pdf") else "html"

        output_format = _resolve_output_format(query)

        # --------- 模式自动判定 ---------
        if isinstance(query, dict):
            payload = dict(query)
            mode = (payload.get("mode") or "").lower().strip()
            # 若未显式给 mode，但提供了 draft/state 路径，也按 files 处理
            if (not mode) and (payload.get("draft_path") or payload.get("query_engine_draft")
                               or payload.get("state_path") or payload.get("query_engine_state")):
                mode = "files"

            # ====== A) HTML 路线（保持原有产线） ======
            if output_format == "html":
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

                # —— 非 files：旧模式 —— #
                query_text = (payload.get("query") or "").strip() or "综合报告"
                custom_template = payload.get("custom_template") or custom_template

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

            # ====== B) DOCX/PDF 路线（原生直出） ======
            # 需配合 model.py & writers/
            if not _WRITER_AVAILABLE:
                return {"ok": False, "error": f"Writers not available: {_WRITER_IMPORT_ERROR}"}

            if mode == "files":
                # 结构化自 state/draft 合并
                draft = payload.get("draft_path") or payload.get("query_engine_draft")
                state = payload.get("state_path") or payload.get("query_engine_state")
                meta = {
                    "title": payload.get("title") or payload.get("query") or "研究报告",
                    "subtitle": payload.get("subtitle"),
                    "author": payload.get("author") or "Auto Researcher",
                    "date": payload.get("date"),
                }
                model = build_model_from_inputs(state_path=state, draft_path=draft, free_text=None, meta_overrides=meta)
            else:
                # 纯文本/Prompt 场景：直接把文本作为正文
                free_text = (payload.get("text") or payload.get("query") or "").strip() or "（无可用正文）"
                model = build_model_from_inputs(state_path=None, draft_path=None, free_text=free_text, meta_overrides={
                    "title": payload.get("title") or payload.get("query") or "研究报告",
                    "author": payload.get("author") or "Auto Researcher",
                    "date": payload.get("date"),
                })

        else:
            # —— query 为字符串 —— #
            if output_format == "html":
                # 旧的 HTML 产线：使用现有 Agent 自行加载上游产物
                query_text = (query or "").strip() or "综合报告"
                cfg = _REPORT_AGENT.config
                forum_log_path = str(Path(cfg.log_file).with_name("forum.log"))
                if not Path(forum_log_path).exists():
                    forum_log_path = cfg.log_file

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

            # —— 字符串 + DOCX/PDF —— #
            if not _WRITER_AVAILABLE:
                return {"ok": False, "error": f"Writers not available: {_WRITER_IMPORT_ERROR}"}

            free_text = (query or "").strip()
            model = build_model_from_inputs(state_path=None, draft_path=None, free_text=free_text, meta_overrides={
                "title": "研究报告",
                "author": "Auto Researcher",
            })

        # ====== 统一的 DOCX/PDF 落盘逻辑 ======
        if output_format in ("docx", "pdf"):
            if not _WRITER_AVAILABLE:
                return {"ok": False, "error": f"Writers not available: {_WRITER_IMPORT_ERROR}"}

            writer = pick_writer(output_format)  # docx_writer / pdf_writer
            cfg = _REPORT_AGENT.config
            out_dir = _final_output_dir(cfg)
            _ensure_dir(out_dir)

            base_name = (model.meta.title or "report").strip().replace(" ", "_")
            out_path = str(Path(out_dir) / f"{base_name}{pick_ext(output_format)}")

            try:
                result_path = writer.write(model, out_path)
                size = os.path.getsize(result_path) if os.path.exists(result_path) else 0
            except Exception as e:
                traceback.print_exc()
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}

            if output_format == "docx":
                return {"ok": True, "result": {
                    "docx_path": _normpath(result_path),
                    "docx_len": size,
                    "report_title": model.meta.title,
                }}
            else:
                return {"ok": True, "result": {
                    "pdf_path": _normpath(result_path),
                    "pdf_len": size,
                    "report_title": model.meta.title,
                }}

        # 理论到不了这里（上面已覆盖 html/docx/pdf）
        return {"ok": False, "error": "unsupported output format"}

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
                "output_dir": _normpath(getattr(cfg, "output_dir", "")),
                "template_dir": _normpath(getattr(cfg, "template_dir", "")),
                "log_file": _normpath(getattr(cfg, "log_file", "")),
                "model": model_info,
                "last_html_path": last_html,
                "writers_available": _WRITER_AVAILABLE,
            })
            if not _WRITER_AVAILABLE:
                info["writers_error"] = _WRITER_IMPORT_ERROR
        return JSONResponse(info, status_code=200)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=200)


@report_router.post("/generate")
def generate_report(payload: Dict[str, Any] = Body(...)):
    """
    异步任务接口（保持原有）：暂未接入 files/docx/pdf 模式，前端需要 files 直出请用 run_report_sync 的 dict 调用，
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
