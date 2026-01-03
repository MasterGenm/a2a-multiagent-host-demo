# pages/conversation.py
# 对话页：支持 naga:/api/chat 管线 + Ollama 流式 + 右侧 Pipeline Debug 面板
# + 报告下载链接（PDF/DOCX）& 记忆链路 demo 展示

import os
import re
import json
from pathlib import Path
from urllib.parse import quote

import httpx
import mesop as me

from state.state import AppState, ChatMessage, is_form
from components.form_render import render_form

# 由主程序在 lifespan 中注入
ollama_service: "OllamaService" = None
security_manager: "SecurityManager" = None
auth_service: "AuthService" = None

# 是否优先走 /api/chat（Naga 管线）
USE_API_CHAT = str(os.getenv("USE_API_CHAT", "true")).lower() in ("1", "true", "yes")
API_HOST = os.getenv("A2A_UI_HOST", "127.0.0.1")
API_PORT = int(os.getenv("A2A_UI_PORT", "12000"))
API_BASE = f"http://{API_HOST}:{API_PORT}"
DEFAULT_USE_MCP = str(os.getenv("USE_MCP", "false")).lower() in ("1", "true", "yes")
SHOW_MEMORY_CONTEXT = str(os.getenv("SHOW_MEMORY_CONTEXT", "false")).lower() in ("1", "true", "yes")

# /api/chat 超时（秒）
API_CHAT_TIMEOUT_S = int(os.getenv("API_CHAT_TIMEOUT_S", "3600"))

# 是否禁用 ConversationServer 回退（True = 只走 /api/chat）
DISABLE_CONVSERVER_FALLBACK = str(os.getenv("DISABLE_CONVSERVER_FALLBACK", "true")).lower() in ("1", "true", "yes")

# 是否在 UI 中显示 Naga Debug 侧栏（生产可以关掉）
NAGA_DEBUG_UI = str(os.getenv("NAGA_DEBUG_UI", "true")).lower() in ("1", "true", "yes")

# 右侧调试侧栏宽度 & 距离右侧的偏移（“往左拉一点”就在这里调）
SIDE_PANEL_WIDTH = 260          # 右侧调试面板固定宽度
SIDE_PANEL_GAP = 16             # 对话区和右侧面板之间预留一点间距

# 报告下载端点（需要后端实现：GET {REPORT_DOWNLOAD_ENDPOINT}?path=...&format=pdf|docx）
# 你也可以让后端直接返回 download_links（绝对 URL），UI 会优先使用。
REPORT_DOWNLOAD_ENDPOINT = os.getenv("REPORT_DOWNLOAD_ENDPOINT", "/api/report/download")


# ============== ConversationServer 兼容保留（Naga 回退链路） ==============
from state.host_agent_service import (
    ensure_conversation_id,
    send_user_text,
    wait_by_pending,
    get_last_agent_reply,
    UpdateAppState,
)


def on_input(e: me.InputBlurEvent):
    me.state(AppState).user_input = e.value


async def on_submit_click(e: me.ClickEvent):
    async for _ in _submit_chat():
        yield


async def on_submit_shortcut(e: me.TextareaShortcutEvent):
    st = me.state(AppState)
    st.user_input = e.value
    yield
    async for _ in _submit_chat():
        yield


def _coerce_conversation_id(obj):
    """尽量从各种形态里提取 conversation_id，避免 NoneType 错误。"""
    if not obj:
        return None
    if isinstance(obj, str):
        return obj.strip() or None
    if isinstance(obj, dict):
        for k in ("conversation_id", "id", "cid"):
            if obj.get(k):
                return str(obj[k])
        return None
    for k in ("conversation_id", "id", "cid"):
        if hasattr(obj, k) and getattr(obj, k):
            return str(getattr(obj, k))
    return None


def _toggle_debug_panel(e: me.ClickEvent):
    st = me.state(AppState)
    st.debug_panel_open = not st.debug_panel_open


def _on_debug_panel_changed(e: me.SidenavOpenedChangedEvent):
    st = me.state(AppState)
    st.debug_panel_open = bool(e.opened)


def _toggle_debug_mode(e: me.ClickEvent):
    st = me.state(AppState)
    st.debug_mode = not st.debug_mode


def _on_debug_filters_change(e):
    st = me.state(AppState)
    vals = set(getattr(e, "values", []) or [])
    st.debug_show_plan = "plan" in vals
    st.debug_show_memory = "memory" in vals
    st.debug_show_language = "language" in vals


def _norm_path_for_url(p: str) -> str:
    # 兼容 Windows 盘符路径；后端需要 URL decode 再做安全校验
    return (p or "").replace("\\", "/")


def _parse_report_cmd(text: str):
    """
    支持：
      /report html xxx
      /report pdf xxx
      /report docx xxx
      /report auto xxx
    返回 (report_output or None, cleaned_text)
    """
    m = re.match(r"^\s*/report\s+(html|pdf|docx|auto)\s*(.*)$", text, flags=re.I)
    if not m:
        return None, text
    fmt = m.group(1).lower()
    rest = (m.group(2) or "").strip()
    return fmt, (rest if rest else text)


# 从 result 文本里兜底提取报告路径（防止后端没回 re_report_path）
_REPORT_PATH_RE = re.compile(
    r"([A-Za-z]:[\\/][^\s\"']+\.(?:html|pdf|docx|md))",
    flags=re.IGNORECASE,
)


def _extract_report_path_from_text(text: str) -> str:
    if not text:
        return ""
    m = _REPORT_PATH_RE.search(text)
    return m.group(1) if m else ""


def _build_report_download_urls(data: dict) -> dict:
    """
    根据 /api/chat 回包里的 re_report_path 生成下载链接。
    - 永远提供 auto（后端可做 fallback，保证“点了就能下到东西”）
    - 再根据实际文件后缀，只提供对应格式（html/pdf/docx/md）
    """
    data = data or {}
    # 若后端已直接给 download_links，则优先使用
    dl = data.get("download_links")
    if isinstance(dl, dict) and dl:
        return dl
    raw_path = (
        data.get("re_report_path")
        or data.get("report_path")
        or data.get("pdf_path")
        or data.get("docx_path")
        or data.get("html_path")
        or ""
    )
    if not raw_path:
        return {}

    path_q = quote(_norm_path_for_url(str(raw_path)), safe="")

    base = API_BASE.rstrip("/")
    ep = REPORT_DOWNLOAD_ENDPOINT
    if not ep.startswith("/"):
        ep = f"/{ep}"

    def u(fmt: str, inline: bool = False) -> str:
        extra = "&inline=1" if inline else ""
        return f"{base}{ep}?path={path_q}&format={fmt}{extra}"

    urls = {"auto": u("auto")}

    ext = Path(str(raw_path)).suffix.lower()
    if ext == ".html":
        urls["html"] = u("html", inline=True)
    elif ext == ".pdf":
        urls["pdf"] = u("pdf")
    elif ext == ".docx":
        urls["docx"] = u("docx")
    elif ext == ".md":
        urls["md"] = u("md")

    return urls


def _maybe_insert_report_download_message(st: AppState, data: dict):
    """
    当 ReportEngine 产出文件时，在对话里插入一条系统提示，方便用户不打开右侧面板也能下载。
    """
    urls = _build_report_download_urls(data)
    if not urls:
        return

    # 避免重复插入：如果最近 6 条里已有 “Download report” 就不再插
    recent = st.messages[-6:] if len(st.messages) > 6 else st.messages
    for m in recent:
        if m.role == "system" and "Download report" in (m.content or ""):
            return

    lines = ["**Download report:**"]

    shown = False
    for key, label in [("html", "HTML"), ("pdf", "PDF"), ("docx", "DOCX"), ("md", "MD")]:
        if urls.get(key):
            lines.append(f"- [{label}]({urls[key]})")
            shown = True

    # 永远给一个 auto 兜底（即便已有其它格式）
    if urls.get("auto"):
        lines.append(f"- [Auto download]({urls['auto']})")

    st.messages.append(ChatMessage(role="system", content="\n".join(lines)))


async def _submit_chat():
    st = me.state(AppState)

    raw_text = (st.user_input or "").strip()
    if not raw_text:
        return

    # ✅ 解析 /report 指令（只影响后端 payload，不影响 UI 显示也可以）
    report_output, cleaned_text = _parse_report_cmd(raw_text)

    is_naga = str(st.selected_model).startswith("naga:")

    # 1) 先把用户消息落到会话（显示原始输入）
    st.user_input = ""
    st.messages.append(ChatMessage(role="user", content=raw_text))
    me.scroll_into_view(key="scroll-to-bottom")
    yield

    # ===================== Naga 分支：走 /api/chat + Pipeline =====================
    if is_naga:
        assistant = ChatMessage(role="model", content="Processing...")
        st.messages.append(assistant)
        yield

        # 优先尝试 /api/chat
        if USE_API_CHAT:
            try:
                timeout = httpx.Timeout(
                    connect=10.0,
                    read=API_CHAT_TIMEOUT_S,
                    write=API_CHAT_TIMEOUT_S,
                    pool=None,
                )
                async with httpx.AsyncClient(timeout=timeout) as client:
                    # ==== 构造多轮对话 history ====
                    # 当前 st.messages 结构： [...历史..., 当前 user(raw), 当前 assistant 占位]
                    if len(st.messages) >= 2:
                        history_source = st.messages[:-2]
                    else:
                        history_source = []

                    history = []
                    for m in history_source:
                        # 只把真正对话的 user/model 传给后端，跳过 system 调试消息
                        if m.role not in ("user", "model", "assistant"):
                            continue
                        role = "assistant" if m.role in ("model", "assistant") else "user"
                        history.append({"role": role, "content": m.content})

                    payload = {
                        "input": cleaned_text,  # ✅ 发送清理后的文本
                        "profile": "naga",
                        "use_mcp": DEFAULT_USE_MCP,
                        "reply_lang": getattr(
                            st,
                            "reply_lang",
                            os.getenv("DEFAULT_REPLY_LANG", "auto"),
                        ),
                        "history": history,
                    }
                    # ✅ 强制报告输出（只要你在 UI 输入 /report pdf xxx）
                    if report_output:
                        payload["report_output"] = report_output
                        # 在 payload 构造后、发请求前（即当前的 if report_output: 块内）加上：
                        payload["force_report"] = True
                        payload["output_format"] = report_output
                        # /report 需要 QE + RE 闭环；force_query=True 会导致 pipeline 跳过 RE
                        payload["force_combo"] = True



                    r = await client.post(f"{API_BASE}/api/chat", json=payload)
                    r.raise_for_status()
                    data = r.json()

                # ========== 同步 pipeline 状态到 AppState ==========
                st.used_query_engine = bool(data.get("used_query_engine"))
                st.used_report_engine = bool(data.get("used_report_engine"))
                st.used_grag_memory = bool(data.get("used_grag_memory"))

                intent_plan = data.get("intent_plan") or {}
                st.last_task = (intent_plan.get("task") or "").lower()

                st.last_report_path = (
                    data.get("re_report_path")
                    or data.get("report_path")
                    or data.get("html_path")
                    or data.get("pdf_path")
                    or data.get("docx_path")
                    or ""
                )

                # ✅ 兜底：如果后端没回 re_report_path，但 result 文本里提到了本地路径，也能出链接
                if not st.last_report_path:
                    st.last_report_path = _extract_report_path_from_text(data.get("result") or "")

                # ✅ 兜底：只要拿到了路径，就认为“可下载”
                if st.last_report_path:
                    st.used_report_engine = True

                # memory_context 可能很长：这里仅保留 500 字符用于侧栏预览
                st.last_memory_snippet = (data.get("memory_context") or "")[:500]

                # 把“记忆命中列表”也塞进 snippet（后端可选返回）
                hits = data.get("memory_hits") or data.get("grag_hits") or data.get("retrieved_chunks")
                if hits and isinstance(hits, list):
                    preview = []
                    for i, h in enumerate(hits[:5], start=1):
                        if isinstance(h, dict):
                            title = h.get("title") or h.get("name") or h.get("id") or f"hit_{i}"
                            score = h.get("score") or h.get("similarity") or ""
                            preview.append(f"{i}. {title} {f'({score})' if score != '' else ''}".strip())
                        else:
                            preview.append(f"{i}. {str(h)[:120]}")
                    if preview:
                        st.last_memory_snippet = (
                            (st.last_memory_snippet or "")
                            + "\n\n**Top memory hits (preview):**\n"
                            + "\n".join([f"- {x}" for x in preview])
                        )

                # 调试模式：往对话里插入 [Plan] / [Language] / [Memory]
                if st.debug_show_plan:
                    plan = data.get("plan") or data.get("intent_plan")
                    if plan:
                        st.messages.append(
                            ChatMessage(
                                role="system",
                                content=(
                                    "[Plan]\n```json\n"
                                    f"{json.dumps(plan, ensure_ascii=False, indent=2)}\n```"
                                ),
                            )
                        )
                        yield

                if st.debug_show_language and "reply_lang" in data:
                    st.messages.append(ChatMessage(role="system", content=f"[Language] {data['reply_lang']}"))
                    yield

                if st.debug_show_memory and SHOW_MEMORY_CONTEXT and data.get("memory_context"):
                    mem = data.get("memory_context") or ""
                    if len(mem) > 500:
                        mem = mem[:500] + "..."
                    st.messages.append(ChatMessage(role="system", content=f"[Memory]\n{mem}"))
                    yield

                # ✅ 只要有 report_path，就插入下载链接（不需要打开右侧面板）
                if st.last_report_path:
                    _maybe_insert_report_download_message(st, {"re_report_path": st.last_report_path})
                    yield

                assistant.content = data.get("result") or "(empty reply)"
                me.focus_component(key="chat_input")
                yield
                return

            except Exception as e:
                if DISABLE_CONVSERVER_FALLBACK:
                    assistant.content = f"(api/chat failed; fallback disabled) {e}"
                    me.focus_component(key="chat_input")
                    yield
                    return
                else:
                    assistant.content = f"(api/chat failed; try legacy fallback) {e}\nprocessing..."
                    yield
                    # 继续走下面 ConversationServer 回退链路

        # ----------------- 回退路径：ConversationServer（老链路） -----------------
        try:
            existing = getattr(st, "current_conversation_id", None)
            cid_hint = _coerce_conversation_id(existing)
            conv_obj = await ensure_conversation_id(cid_hint)
            cid = _coerce_conversation_id(conv_obj)
            if not cid:
                raise RuntimeError("ensure_conversation_id returned invalid conversation id")
            st.current_conversation_id = cid

            mid, _ = await send_user_text(cid, raw_text)
        except Exception as e:
            assistant.content = f"发送失败：{e}"
            yield
            return

        await wait_by_pending(mid, st.current_conversation_id, timeout_s=60.0, poll_interval=0.6)
        await UpdateAppState(st, st.current_conversation_id)
        try:
            kind, payload = await get_last_agent_reply(st.current_conversation_id)
        except Exception as e:
            assistant.content = f"获取回复失败：{e}"
            yield
            return

        if kind == "form":
            assistant.content = "已为你准备了一个表单，请填写必要信息后提交。"
        elif kind == "text":
            assistant.content = payload if payload else "(空回复)"
        else:
            assistant.content = "未获取到回复，请检查后端日志"

        me.focus_component(key="chat_input")
        yield
        return

    # ===================== 非 Naga：Ollama 流式 =====================
    if st.ollama_connected is False:
        st.messages.append(
            ChatMessage(
                role="system",
                content="Ollama 未连接。请选择 naga:default 或连接 Ollama 后再试。",
            )
        )
        yield
        return

    assistant = ChatMessage(role="model", content="Processing...")
    st.messages.append(assistant)
    yield

    full = ""
    messages_for_api = [{"role": m.role, "content": m.content} for m in st.messages[:-1]]
    options = {"temperature": st.temperature, "top_p": st.top_p, "top_k": st.top_k}

    async for chunk in ollama_service.stream_chat(st.selected_model, messages_for_api, options):
        full += chunk
        assistant.content = full + "…"
        yield

    assistant.content = full
    me.focus_component(key="chat_input")
    yield


# ========================= UI 组件 =========================


def _render_message_content(content: str):
    """统一处理纯文本 + 内嵌图片 markdown 渲染。"""
    if "![生成的图片]" in content:
        parts = re.split(r"(!\[.*?\]\(.*?\))", content)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if part.startswith("![") and part.endswith(")"):
                m = re.search(r"!\[.*?\]\((.*?)\)", part)
                if m:
                    me.image(src=m.group(1), style=me.Style(width="100%"))
            else:
                me.markdown(part)
    else:
        me.markdown(content)


@me.component
def user_message(message: ChatMessage):
    with me.box(
        style=me.Style(
            display="flex",
            justify_content="flex-end",
            margin=me.Margin.symmetric(vertical=10),
        )
    ):
        with me.box(
            style=me.Style(
                background=me.theme_var("primary-container"),
                color=me.theme_var("on-primary-container"),
                padding=me.Padding.symmetric(vertical=10, horizontal=14),
                border_radius=18,
                max_width="80%",
            )
        ):
            _render_message_content(message.content)


@me.component
def model_message(message: ChatMessage):
    # system 消息用更弱的样式区分一下（方便 demo：Plan/Memory/Download 链路）
    if message.role == "system":
        with me.box(
            style=me.Style(
                display="flex",
                justify_content="center",
                margin=me.Margin.symmetric(vertical=8),
            )
        ):
            with me.box(
                style=me.Style(
                    background=me.theme_var("surface-container-low"),
                    color=me.theme_var("on-surface-variant"),
                    padding=me.Padding.symmetric(vertical=8, horizontal=12),
                    border_radius=14,
                    max_width="85%",
                )
            ):
                _render_message_content(message.content)
        return

    with me.box(
        style=me.Style(
            display="flex",
            justify_content="flex-start",
            margin=me.Margin.symmetric(vertical=10),
        )
    ):
        with me.box(
            style=me.Style(
                background=me.theme_var("surface-container-highest"),
                color=me.theme_var("on-surface"),
                padding=me.Padding.symmetric(vertical=10, horizontal=14),
                border_radius=18,
                max_width="80%",
            )
        ):
            _render_message_content(message.content)


@me.component
def conversation_page(state: AppState):
    try:
        # 只在 Naga 模式下展示调试侧栏（且受 env 控制）
        is_naga_model = str(state.selected_model).startswith("naga:")
        show_debug_ui = NAGA_DEBUG_UI and is_naga_model

        # ===== 整体：对话区 + 右侧 Pipeline 竖栏（左右两栏） =====
        with me.box(
            style=me.Style(
                width="100%",
                height="100%",
                display="flex",
                flex_direction="row",
                overflow_x="hidden",
            )
        ):
            # ---------- 左：主对话区 ----------
            with me.box(
                style=me.Style(
                    flex_grow=1,
                    display="flex",
                    flex_direction="column",
                    overflow_x="hidden",
                )
            ):
                # 顶部标题 + 调试按钮
                with me.box(
                    style=me.Style(
                        display="flex",
                        justify_content="space-between",
                        align_items="center",
                        padding=me.Padding.symmetric(vertical=4, horizontal=24),
                    )
                ):
                    me.text("对话", type="headline-6")
                    if show_debug_ui:
                        with me.content_button(type="icon", on_click=_toggle_debug_panel):
                            me.icon("tune")

                # 消息区
                with me.box(style=me.Style(flex_grow=1, overflow_y="auto")):
                    # 中间的聊天区域：限制一个最大宽度，但用掉左边所有空间
                    with me.box(
                        style=me.Style(
                            padding=me.Padding.all(24),
                            max_width="1200px",
                            margin=me.Margin.symmetric(horizontal="auto"),
                        )
                    ):
                        if not state.messages and not state.legacy_messages:
                            me.text(
                                "开始聊天吧～",
                                style=me.Style(
                                    font_style="italic",
                                    color=me.theme_var("on-surface-variant"),
                                ),
                            )

                        for msg in state.messages:
                            if msg.role == "user":
                                user_message(msg)
                            else:
                                model_message(msg)

                        for msg in state.legacy_messages:
                            if is_form(msg):
                                render_form(msg, state)

                        with me.box(key="scroll-to-bottom", style=me.Style(height=1)):
                            pass

                # 输入区
                with me.box(
                    style=me.Style(
                        flex_shrink=0,
                        padding=me.Padding.symmetric(vertical=8, horizontal=24),
                        border=me.Border(
                            top=me.BorderSide(
                                style="solid",
                                width=1,
                                color=me.theme_var("outline-variant"),
                            )
                        ),
                    )
                ):
                    with me.box(
                        style=me.Style(
                            max_width="900px",
                            margin=me.Margin.symmetric(horizontal="auto"),
                            display="flex",
                            align_items="flex_end",
                            gap=8,
                        )
                    ):
                        me.native_textarea(
                            key="chat_input",
                            on_blur=on_input,
                            placeholder="输入问题 (Shift+Enter 发送)。\n也可用：/report pdf xxx | /report html xxx | /report docx xxx",
                            autosize=True,
                            min_rows=1,
                            max_rows=5,
                            shortcuts={me.Shortcut(shift=True, key="Enter"): on_submit_shortcut},
                            style=me.Style(flex_grow=1, width="100%"),
                        )
                        disabled_send = (not (state.user_input or "").strip()) or (
                            not str(state.selected_model).startswith("naga:")
                            and not state.ollama_connected
                        )
                        with me.content_button(
                            on_click=on_submit_click,
                            disabled=disabled_send,
                            type="icon",
                            style=me.Style(flex_shrink=0),
                        ):
                            me.icon("send")

            # ---------- 右：Pipeline Debug 竖栏 ----------
            if show_debug_ui and state.debug_panel_open:
                with me.box(
                    style=me.Style(
                        width=f"{SIDE_PANEL_WIDTH}px",
                        flex_shrink=0,
                        margin=me.Margin(left=SIDE_PANEL_GAP),
                        height="100%",
                    )
                ):
                    _debug_side_panel(state)

    except Exception as e:
        import logging

        logging.exception(e)
        me.text(f"UI 渲染异常：{e}")


@me.component
def _debug_side_panel(state: AppState):
    with me.box(
        style=me.Style(
            width="100%",
            padding=me.Padding.all(12),
            border=me.Border(
                left=me.BorderSide(
                    style="solid",
                    width=1,
                    color=me.theme_var("outline-variant"),
                )
            ),
            background=me.theme_var("surface"),
            display="flex",
            flex_direction="column",
            gap=8,
            height="100%",
            overflow_y="auto",
        )
    ):
        # ========= Pipeline chain demo =========
        me.text("Pipeline Chain", type="subtitle-1")
        with me.box(
            style=me.Style(
                padding=me.Padding.all(8),
                border_radius=10,
                background=me.theme_var("surface-container-low"),
                display="flex",
                flex_direction="column",
                gap=6,
            )
        ):
            me.text(f"1) Intent/Task: {state.last_task or 'unknown'}", type="body-2")
            me.text(f"2) Memory: {'hit' if state.used_grag_memory else 'miss'}", type="body-2")
            me.text(f"3) QueryEngine: {'used' if state.used_query_engine else 'not used'}", type="body-2")
            me.text(f"4) ReportEngine: {'used' if state.used_report_engine else 'not used'}", type="body-2")
            me.text("5) Reply: shown in left chat", type="body-2")

        me.divider()

        # ========= 状态块 =========
        me.text("Pipeline Status", type="subtitle-1")
        with me.box(
            style=me.Style(
                padding=me.Padding.all(8),
                border_radius=10,
                background=me.theme_var("surface-container-low"),
                display="flex",
                flex_direction="column",
                gap=4,
            )
        ):
            me.text(f"Task: {state.last_task or 'unknown'}", type="body-2")
            me.text(f"QueryEngine: {'used' if state.used_query_engine else 'not used'}", type="body-2")
            me.text(f"ReportEngine: {'used' if state.used_report_engine else 'not used'}", type="body-2")
            me.text(f"GRAG Memory: {'hit' if state.used_grag_memory else 'miss'}", type="body-2")
            if state.used_grag_memory:
                me.text("This reply referenced knowledge base", style=me.Style(color=me.theme_var("primary")))

            if getattr(state, "last_report_path", ""):
                me.text("Report:", type="body-2")
                me.markdown(f"`{state.last_report_path}`")

        me.divider()

        # ========= Downloads =========
        if getattr(state, "last_report_path", ""):
            me.text("Downloads", type="subtitle-1")

            urls = _build_report_download_urls({"re_report_path": state.last_report_path})

            shown = False
            for key, label in [("html", "HTML"), ("pdf", "PDF"), ("docx", "DOCX"), ("md", "MD")]:
                if urls.get(key):
                    me.markdown(f"- [{label}]({urls[key]})")
                    shown = True

            if (not shown) and urls.get("auto"):
                me.markdown(f"- [Report]({urls['auto']})")

            me.divider()

        # ========= 调试控制 =========
        me.text("Debug Options", type="subtitle-1")
        with me.content_button(on_click=_toggle_debug_mode, style=me.Style(padding=me.Padding.all(6))):
            label = "Debug mode: ON" if state.debug_mode else "Debug mode: OFF"
            me.text(label, type="body-2")

        me.button_toggle(
            value=[
                v
                for v, enabled in (
                    ("plan", state.debug_show_plan),
                    ("language", state.debug_show_language),
                    ("memory", state.debug_show_memory),
                )
                if enabled
            ],
            buttons=[
                me.ButtonToggleButton(label="Plan", value="plan"),
                me.ButtonToggleButton(label="Language", value="language"),
                me.ButtonToggleButton(label="Memory", value="memory"),
            ],
            multiple=True,
            hide_selection_indicator=False,
            on_change=_on_debug_filters_change,
        )

        # ========= Memory snippet 预览（独立小滚动框） =========
        if state.used_grag_memory and getattr(state, "last_memory_snippet", ""):
            me.text("Memory snippet:", type="caption")
            with me.box(
                style=me.Style(
                    max_height=220,
                    overflow_y="auto",
                    padding=me.Padding.all(6),
                    background=me.theme_var("surface-container-low"),
                    border_radius=10,
                )
            ):
                me.markdown(state.last_memory_snippet)
