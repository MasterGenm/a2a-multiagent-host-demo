# pages/conversation.py —— 适配新版主控（优先 /api/chat；可选禁用旧链路回退；Ollama 走流式）
import os, re, json, httpx
import mesop as me
from state.state import AppState, ChatMessage, is_form
from components.form_render import render_form

# 由主程序在 lifespan 注入
ollama_service: "OllamaService" = None
security_manager: "SecurityManager" = None
auth_service: "AuthService" = None

# —— 可调开关：是否优先走 /api/chat（默认 True）——
USE_API_CHAT = str(os.getenv("USE_API_CHAT", "true")).lower() in ("1", "true", "yes")
API_HOST = os.getenv("A2A_UI_HOST", "127.0.0.1")
API_PORT = int(os.getenv("A2A_UI_PORT", "12000"))
API_BASE = f"http://{API_HOST}:{API_PORT}"
DEFAULT_USE_MCP = str(os.getenv("USE_MCP", "false")).lower() in ("1", "true", "yes")

# —— 新增：API 调用超时（秒），默认 3600 秒，覆盖 60 秒导致 Combo 超时的问题 —— 
API_CHAT_TIMEOUT_S = int(os.getenv("API_CHAT_TIMEOUT_S", "3600"))

# —— 新增：是否禁用 ConversationServer 旧链路回退（默认禁用，避免 NoneType 报错）——
DISABLE_CONVSERVER_FALLBACK = str(os.getenv("DISABLE_CONVSERVER_FALLBACK", "true")).lower() in ("1","true","yes")

# ============== ConversationServer 兼容保留（Naga 回退路径） ==============
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

# —— 抽取/规整 conversation_id，避免 NoneType 错误 ——
def _coerce_conversation_id(obj):
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

async def _submit_chat():
    st = me.state(AppState)
    text = (st.user_input or "").strip()
    if not text:
        return

    is_naga = str(st.selected_model).startswith("naga:")

    # 1) 先落地用户消息
    st.user_input = ""
    st.messages.append(ChatMessage(role="user", content=text))
    me.scroll_into_view(key="scroll-to-bottom")
    yield

    # ===================== Naga 分支（新主控） =====================
    if is_naga:
        assistant = ChatMessage(role="model", content="处理中…")
        st.messages.append(assistant)
        yield

        if USE_API_CHAT:
            try:
                timeout = httpx.Timeout(
                    connect=10.0,
                    read=API_CHAT_TIMEOUT_S,
                    write=API_CHAT_TIMEOUT_S,
                    pool=None
                )
                async with httpx.AsyncClient(timeout=timeout) as client:
                    r = await client.post(f"{API_BASE}/api/chat", json={
                        "input": text,
                        "profile": "naga",
                        "use_mcp": DEFAULT_USE_MCP,
                    })
                    r.raise_for_status()
                    data = r.json()

                # 可视化 Plan
                plan = data.get("plan")
                if plan:
                    st.messages.append(ChatMessage(
                        role="system",
                        content=f"【规划】\n```json\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n```"
                    ))
                    yield

                assistant.content = data.get("result") or "（空回复）"
                me.focus_component(key="chat_input")
                yield
                return
            except Exception as e:
                if DISABLE_CONVSERVER_FALLBACK:
                    assistant.content = f"（/api/chat 调用失败，已停止回退旧链路）{e}"
                    me.focus_component(key="chat_input")
                    yield
                    return
                else:
                    assistant.content = f"（/api/chat 调用失败，尝试回退旧链路）{e}\n继续处理中…"
                    yield
                    # 继续走下面的旧链路回退

        # ----------------- 回退路径：ConversationServer（旧链路） -----------------
        try:
            existing = getattr(st, "current_conversation_id", None)
            cid_hint = _coerce_conversation_id(existing)
            conv_obj = await ensure_conversation_id(cid_hint)
            cid = _coerce_conversation_id(conv_obj)
            if not cid:
                raise RuntimeError("ensure_conversation_id 返回异常，无法获取会话ID")
            st.current_conversation_id = cid

            mid, _ = await send_user_text(cid, text)
        except Exception as e:
            assistant.content = f"发送失败：{e}"
            yield
            return

        await wait_by_pending(mid, st.current_conversation_id, timeout_s=60.0, poll_interval=0.6)
        await UpdateAppState(st, st.current_conversation_id)
        try:
            kind, payload = await get_last_agent_reply(st.current_conversation_id)
        except Exception as e:
            assistant.content = f"（获取回复失败）{e}"
            yield
            return

        if kind == 'form':
            assistant.content = "已为你准备了一个表单，请填写必要信息后提交。"
        elif kind == 'text':
            assistant.content = payload if payload else "（空回复）"
        else:
            assistant.content = "（未获取到回复，请检查后端日志）"

        me.focus_component(key="chat_input")
        yield
        return

    # ===================== 非 Naga：Ollama 流式 =====================
    if st.ollama_connected is False:
        st.messages.append(ChatMessage(role="system", content="Ollama 未连接。请选择 naga:default 或连接 Ollama 后再试。"))
        yield
        return

    assistant = ChatMessage(role="model", content="")
    st.messages.append(assistant)
    yield

    full = ""
    messages_for_api = [{"role": m.role, "content": m.content} for m in st.messages[:-1]]
    options = {"temperature": st.temperature, "top_p": st.top_p, "top_k": st.top_k}

    async for chunk in ollama_service.stream_chat(st.selected_model, messages_for_api, options):
        full += chunk
        assistant.content = full + "▌"
        yield

    assistant.content = full
    me.focus_component(key="chat_input")
    yield

# ========================= UI 组件 =========================
@me.component
def user_message(message: ChatMessage):
    with me.box(style=me.Style(display="flex", justify_content="flex-end", margin=me.Margin.symmetric(vertical=10))):
        with me.box(style=me.Style(
            background=me.theme_var("primary-container"),
            color=me.theme_var("on-primary-container"),
            padding=me.Padding.symmetric(vertical=10, horizontal=14),
            border_radius=18,
            max_width="80%",
        )):
            if "![生成的图片]" in message.content:
                parts = re.split(r'(!\[.*?\]\(.*?\))', message.content)
                for part in parts:
                    if part.startswith('![') and part.endswith(')'):
                        m = re.search(r'!\[.*?\]\((.*?)\)', part)
                        if m: me.image(src=m.group(1), style=me.Style(width="100%"))
                    elif part.strip():
                        me.markdown(part.strip())
            else:
                me.markdown(message.content)

@me.component
def model_message(message: ChatMessage):
    with me.box(style=me.Style(display="flex", justify_content="flex-start", margin=me.Margin.symmetric(vertical=10))):
        with me.box(style=me.Style(
            background=me.theme_var("surface-container-highest"),
            color=me.theme_var("on-surface"),
            padding=me.Padding.symmetric(vertical=10, horizontal=14),
            border_radius=18,
            max_width="80%",
        )):
            if "![生成的图片]" in message.content:
                parts = re.split(r'(!\[.*?\]\(.*?\))', message.content)
                for part in parts:
                    if part.startswith('![') and part.endswith(')'):
                        m = re.search(r'!\[.*?\]\((.*?)\)', part)
                        if m: me.image(src=m.group(1), style=me.Style(width="100%"))
                    elif part.strip():
                        me.markdown(part.strip())
            else:
                me.markdown(message.content)

@me.component
def conversation_page(state: AppState):
    with me.box(style=me.Style(width="100%", height="100%", display="flex", flex_direction="column")):
        # 消息区
        with me.box(style=me.Style(flex_grow=1, overflow_y="auto")):
            with me.box(style=me.Style(padding=me.Padding.all(24), max_width="900px", margin=me.Margin.symmetric(horizontal="auto"))):
                if not state.messages and not state.legacy_messages:
                    me.text("开始聊天吧～", style=me.Style(font_style="italic", color=me.theme_var("on-surface-variant")))
                for msg in state.messages:
                    if msg.role == "user":
                        user_message(msg)
                    else:
                        model_message(msg)

                # ✅ 表单通过 legacy_messages 渲染（保持原逻辑）
                for msg in state.legacy_messages:
                    if is_form(msg):
                        render_form(msg, state)

                with me.box(key="scroll-to-bottom", style=me.Style(height=1)):
                    pass

        # 输入区
        with me.box(style=me.Style(
            flex_shrink=0,
            padding=me.Padding.symmetric(vertical=8, horizontal=24),
            border=me.Border(top=me.BorderSide(style="solid", width=1, color=me.theme_var("outline-variant"))),
        )):
            with me.box(style=me.Style(
                max_width="900px",
                margin=me.Margin.symmetric(horizontal="auto"),
                display="flex", align_items="flex-end", gap=8,
            )):
                me.native_textarea(
                    key="chat_input",
                    on_blur=on_input,
                    placeholder="输入问题 (Shift+Enter 发送).",
                    autosize=True, min_rows=1, max_rows=5,
                    shortcuts={me.Shortcut(shift=True, key="Enter"): on_submit_shortcut},
                    style=me.Style(flex_grow=1, width="100%"),
                )
                with me.content_button(
                    on_click=on_submit_click,
                    disabled=(not me.state(AppState).user_input.strip())
                             or (not str(me.state(AppState).selected_model).startswith("naga:")
                                 and not me.state(AppState).ollama_connected),
                    type="icon", style=me.Style(flex_shrink=0)
                ):
                    me.icon("send")
