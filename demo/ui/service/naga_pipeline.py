"""
Lightweight orchestration demo, inspired by TradingAgents.
- Define a unified state object (NagaState).
- Provide node functions (planner -> memory -> QE -> RE -> synthesize).
- Non-invasive: can be wired into /api/chat, but keeps fallbacks possible.
"""

from __future__ import annotations

import os
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from service.utils.intent_parser import IntentParser

try:
    # Query/Report entry points
    from service.QueryEngine.flask_interface import run_query_sync
    from service.ReportEngine.flask_interface import run_report_sync
except Exception as e:  # pragma: no cover
    run_query_sync = None  # type: ignore
    run_report_sync = None  # type: ignore
    logging.warning("Query/Report entry import failed: %s", e)

try:
    # Optional GRAG memory
    from summer_memory.memory_manager import memory_manager
except Exception:  # pragma: no cover
    memory_manager = None  # type: ignore

logger = logging.getLogger(__name__)


def _resolve_report_output_format(v: Optional[str] = None) -> str:
    c = (v or os.getenv("REPORTENGINE_OUTPUT") or "html").lower().strip()
    if c == "docx":
        return "docx"
    if c == "pdf":
        return "pdf"
    return "html"



# -------------------------
# Unified state definition
# -------------------------
@dataclass
class NagaState:
    user_input: str
    report_output: Optional[str] = None
    # intent outputs
    intent: Dict[str, Any] | None = None
    qe_inputs: Dict[str, Any] | None = None
    # memory context
    memory_context: str = ""
    # QE / RE artifacts
    qe_summary: str | None = None
    qe_draft_path: str | None = None
    qe_state_path: str | None = None
    re_template: str | None = None
    re_report_path: str | None = None
    # final reply
    final_reply: str = ""
    # flags
    force_query: bool = False
    force_report: bool = False
    force_combo: bool = False


# -------------------------
# Node functions
# -------------------------
def planner_node(state: NagaState) -> NagaState:
    """Run intent parser to get structured intent and QE inputs."""
    parser = IntentParser()
    state.intent = parser.parse(state.user_input)
    state.qe_inputs = parser.to_query_engine_inputs(state.intent)
    return state


async def memory_retrieve_node(state: NagaState) -> NagaState:
    """Call GRAG memory (if enabled) and write context."""
    if memory_manager is None or not getattr(memory_manager, "enabled", False):
        return state
    try:
        result = await memory_manager.query_memory(state.user_input)
        if result:
            state.memory_context = str(result)
    except Exception as e:  # pragma: no cover
        logger.warning("GRAG retrieval failed: %s", e)
    return state


async def query_engine_node(state: NagaState) -> NagaState:
    """Call QueryEngine (run_query_sync) and record outputs."""
    # 若强制报告但没有要求 combo/QE，则默认跳过 QE；
    # 但当 force_query 也为 True 时，仍应执行 QE 以丰富报告内容。
    if state.force_report and not state.force_combo and not state.force_query:
        state.qe_summary = "(skip QE: force_report=true)"
        return state

    if run_query_sync is None:
        state.qe_summary = "[QE unavailable] run_query_sync not imported"
        return state

    # Build QE payload with intent and memory
    payload_lines = [f"[User request]\n{state.user_input}"]
    if state.intent:
        payload_lines.append(f"[Intent]\n{state.intent}")
    if state.qe_inputs:
        payload_lines.append(f"[QE inputs]\n{state.qe_inputs}")
    if state.memory_context:
        payload_lines.append(f"[Memory]\n{state.memory_context}")
    qe_payload = "\n\n".join(payload_lines)

    try:
        res: Dict[str, Any] = await run_query_sync(
            qe_payload,
            save_report=True,
            timeout_s=300.0,
        )
        if not res.get("ok"):
            state.qe_summary = f"[QE failed] {res.get('error', 'unknown')}"
            return state

        result = res.get("result") or {}
        state.qe_summary = result.get("report_md") or "[QE done] (no summary returned)"
        state.qe_draft_path = result.get("draft_path")
        state.qe_state_path = result.get("state_path")
    except Exception as e:  # pragma: no cover
        state.qe_summary = f"[QE exception] {type(e).__name__}: {e}"
    return state


def _choose_template(user_input: str) -> str:
    """Simple template chooser; extend as needed."""
    text = user_input.lower()
    if any(k in text for k in ["sentiment", "public opinion", "yuqing", "emotion"]):
        return "sentiment_monitoring.md"
    if any(k in text for k in ["competition", "industry", "pattern", "geju", "jingzheng"]):
        return "competition_analysis.md"
    if any(k in text for k in ["fintech", "jinrongkeji", "trend", "technology"]):
        return "fintech_trends.md"
    return "auto-selected-template"


async def report_engine_node(state: NagaState) -> NagaState:
    """Call ReportEngine (run_report_sync) and record outputs."""
    if state.force_query and not state.force_combo:
        state.re_template = "(skip RE: force_query=true)"
        return state

    if run_report_sync is None:
        state.re_template = "[RE unavailable]"
        state.re_report_path = ""
        return state

    if not (state.qe_draft_path or state.qe_state_path):
        # 如果强制生成报告但没有 QE 草稿/状态，直接用原文调用 ReportEngine
        if getattr(state, "force_report", False):
            template = _choose_template(state.user_input)
            out_fmt = _resolve_report_output_format(getattr(state, "report_output", None))
            payload: Dict[str, Any] = {
                "text": state.user_input,
                "custom_template": template,
                "output_format": out_fmt,
            }
            try:
                res: Dict[str, Any] = await run_report_sync(payload, timeout_s=240.0)
                if not res.get("ok"):
                    state.re_template = template
                    state.re_report_path = ""
                    state.qe_summary = (state.qe_summary or "") + f"\n[RE failed] {res.get('error', 'unknown')}"
                    return state
                result = res.get("result") or {}
                state.re_report_path = (
                    result.get("html_path") or result.get("docx_path") or result.get("pdf_path") or ""
                )
                state.re_template = template
                return state
            except Exception as e:  # pragma: no cover
                state.re_template = template
                state.re_report_path = ""
                state.qe_summary = (state.qe_summary or "") + f"\n[RE exception] {type(e).__name__}: {e}"
                return state

        state.re_template = "(RE skipped: draft/state missing)"
        state.re_report_path = ""
        return state

    template = _choose_template(state.user_input)
    out_fmt = _resolve_report_output_format(getattr(state, "report_output", None))
    payload: Dict[str, Any] = {
        "mode": "files",
        "query": state.user_input,
        "draft_path": state.qe_draft_path,
        "state_path": state.qe_state_path,
        "custom_template": template,
        "output_format": out_fmt,
        "save_html": (out_fmt == "html"),
    }

    try:
        res: Dict[str, Any] = await run_report_sync(payload, timeout_s=180.0)
        if not res.get("ok"):
            state.re_template = template
            state.re_report_path = ""
            state.qe_summary = (state.qe_summary or "") + f"\n[RE failed] {res.get('error', 'unknown')}"
            return state

        result = res.get("result") or {}
        state.re_report_path = (
            result.get("html_path")
            or result.get("docx_path")
            or result.get("pdf_path")
            or ""
        )
        state.re_template = template
    except Exception as e:  # pragma: no cover
        state.re_template = template
        state.re_report_path = ""
        state.qe_summary = (state.qe_summary or "") + f"\n[RE exception] {type(e).__name__}: {e}"
    return state


def synthesize_node(state: NagaState) -> NagaState:
    """Merge previous outputs into a final reply string."""
    parts = []
    if state.qe_summary:
        parts.append(state.qe_summary)
    if state.re_report_path:
        parts.append(f"report ready: {state.re_report_path} (template: {state.re_template})")
    if not parts:
        parts.append("no result generated; please check settings or switches.")
    state.final_reply = "\n\n".join(parts)
    return state


# -------------------------
# Simple pipeline runner
# -------------------------
def run_pipeline(
    user_input: str,
    report_output: Optional[str] = None,
    *,
    force_query: bool = False,
    force_report: bool = False,
    force_combo: bool = False,
) -> NagaState:
    """
    Keep a sync version for quick local tests.
    NOTE: uses asyncio.run, do not call when an event loop is already running.
    """
    return asyncio.run(
        run_pipeline_async(
            user_input,
            report_output=report_output,
            force_query=force_query,
            force_report=force_report,
            force_combo=force_combo,
        )
    )


async def run_pipeline_async(
    user_input: str,
    report_output: Optional[str] = None,
    *,
    force_query: bool = False,
    force_report: bool = False,
    force_combo: bool = False,
) -> NagaState:
    """
    Async pipeline: planner -> memory -> QE -> RE -> synthesize.
    Safe to call in /api/chat; errors are captured into state for graceful fallback.
    """
    state = NagaState(
        user_input=user_input,
        report_output=report_output,
        force_query=force_query,
        force_report=force_report,
        force_combo=force_combo,
    )
    # 1) intent
    state = planner_node(state)
    # 2) memory (optional)
    state = await memory_retrieve_node(state)
    if os.getenv("PIPELINE_DEBUG", "0").lower() in ("1", "true", "yes"):
        logger.debug("[Pipeline] memory_context: %s", state.memory_context[:200] if state.memory_context else None)
    # 3) QE
    state = await query_engine_node(state)
    if os.getenv("PIPELINE_DEBUG", "0").lower() in ("1", "true", "yes"):
        logger.debug("[Pipeline] qe_draft_path=%s, qe_state_path=%s", state.qe_draft_path, state.qe_state_path)
    # 4) RE
    state = await report_engine_node(state)
    if os.getenv("PIPELINE_DEBUG", "0").lower() in ("1", "true", "yes"):
        logger.debug("[Pipeline] re_report_path=%s, re_template=%s", state.re_report_path, state.re_template)
    # 5) synthesize
    state = synthesize_node(state)
    # 6) persist conversation to GRAG (best-effort)
    if memory_manager is not None and getattr(memory_manager, "enabled", False):
        try:
            # 记忆写入：优先 QE 摘要，其次最终回复，适当截断避免爆长
            to_store = state.qe_summary or state.final_reply
            if to_store and len(to_store) > 4000:
                to_store = to_store[:4000] + "\n...[truncated]"
            await memory_manager.add_conversation_memory(
                user_input=state.user_input,
                ai_response=to_store or "",
            )
        except Exception as e:  # pragma: no cover
            logger.warning("GRAG write failed: %s", e)
    return state


__all__ = [
    "NagaState",
    "planner_node",
    "memory_retrieve_node",
    "query_engine_node",
    "report_engine_node",
    "synthesize_node",
    "run_pipeline",
    "run_pipeline_async",
]
