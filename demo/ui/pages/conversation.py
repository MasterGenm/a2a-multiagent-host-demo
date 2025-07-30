# pages/conversation.py (最终、完整、可运行版)

import uuid
import mesop as me
from typing import List, Dict

# 从 state.state 一次性导入所有需要的类和函数
from state.state import AppState, ChatMessage, AgentTask, StateMessage, is_form, form_sent

# 导入可重用的组件
from components.form_render import render_form

# =================================================================
# 依赖注入：这些服务实例由主应用注入
# =================================================================
ollama_service: "OllamaService"
security_manager: "SecurityManager"
auth_service: "AuthService"

# =================================================================
# 事件处理器
# =================================================================
def on_input(e: me.InputBlurEvent):
    """当用户输入框失去焦点时，更新状态。"""
    me.state(AppState).user_input = e.value

async def on_submit_click(e: me.ClickEvent):
    """点击发送按钮的事件处理器。"""
    async for _ in _submit_chat():
        yield

async def on_submit_shortcut(e: me.TextareaShortcutEvent):
    """按下快捷键（Shift+Enter）的事件处理器。"""
    state = me.state(AppState)
    state.user_input = e.value
    yield
    async for _ in _submit_chat():
        yield

async def _submit_chat():
    """
    核心聊天提交逻辑，作为一个异步生成器。
    """
    state = me.state(AppState)
    # 增加 strip() 来处理只有空格的输入
    if not state.user_input.strip() or state.ollama_connected is False:
        return

    # 1. 准备并显示用户消息
    prompt = state.user_input
    state.user_input = "" # 立即清空输入框
    state.messages.append(ChatMessage(role="user", content=prompt))
    me.scroll_into_view(key="scroll-to-bottom")
    yield # 更新UI以显示用户消息

    # 2. 准备机器人消息占位符并流式接收响应
    assistant_message = ChatMessage(role="model", content="") # <-- 已修复初始化错误
    state.messages.append(assistant_message)
    yield # 更新UI以显示空的机器人消息气泡

    full_response = ""
    messages_for_api = [{"role": msg.role, "content": msg.content} for msg in state.messages[:-1]]
    options = {"temperature": state.temperature, "top_p": state.top_p, "top_k": state.top_k}
    
    # 异步迭代Ollama服务的流式响应
    async for chunk in ollama_service.stream_chat(state.selected_model, messages_for_api, options):
        full_response += chunk
        assistant_message.content = full_response + "▌" # 添加光标以模拟打字效果
        yield # 每次收到数据块都更新UI
    
    assistant_message.content = full_response # 移除光标
    me.focus_component(key="chat_input") # 完成后重新聚焦到输入框
    yield # 最终UI更新

# =================================================================
# UI 子组件
# =================================================================
@me.component
def user_message(message: ChatMessage):
    """渲染用户消息气泡。"""
    with me.box(style=me.Style(display="flex", justify_content="flex-end", margin=me.Margin.symmetric(vertical=10))):
        with me.box(style=me.Style(
            background=me.theme_var("primary-container"),
            color=me.theme_var("on-primary-container"),
            padding=me.Padding.symmetric(vertical=10, horizontal=14),
            border_radius=18,
            max_width="80%",
        )):
            me.markdown(message.content)

@me.component
def model_message(message: ChatMessage):
    """渲染模型（机器人）消息气泡。"""
    with me.box(style=me.Style(display="flex", justify_content="flex-start", margin=me.Margin.symmetric(vertical=10))):
        with me.box(style=me.Style(
            background=me.theme_var("surface-container-highest"),
            color=me.theme_var("on-surface"),
            padding=me.Padding.symmetric(vertical=10, horizontal=14),
            border_radius=18,
            max_width="80%",
        )):
            me.markdown(message.content)

# =================================================================
# 页面主组件
# =================================================================
@me.component
def conversation_page(state: AppState):
    """
    最终版的对话页面，布局和组件化完全参考 Fancy Chat 示例。
    """
    with me.box(style=me.Style(width="100%", height="100%", display="flex", flex_direction="column")):
        # 聊天历史记录显示区域
        with me.box(style=me.Style(flex_grow=1, overflow_y="auto")):
            with me.box(style=me.Style(padding=me.Padding.all(24), max_width="900px", margin=me.Margin.symmetric(horizontal="auto"))):
                if not state.messages and not state.legacy_messages:
                    me.text("臣妾乃大理寺少卿甄远道之女甄嬛，今日得见，不知有何可以帮到您？", style=me.Style(font_style="italic", color=me.theme_var("on-surface-variant")))
                
                # 渲染新的流式消息
                for msg in state.messages:
                    if msg.role == "user":
                        user_message(msg)
                    else:
                        model_message(msg)

                # 兼容渲染旧的表单消息
                for msg in state.legacy_messages:
                    if is_form(msg): render_form(msg, state)
                    # 可以在这里添加对其他旧版消息的渲染逻辑

                # 用于滚动到底部的空 div
                with me.box(key="scroll-to-bottom", style=me.Style(height=1)):
                    pass

        # 输入区域
        with me.box(style=me.Style(flex_shrink=0, padding=me.Padding.symmetric(vertical=8, horizontal=24), border=me.Border(top=me.BorderSide(style="solid", width=1, color=me.theme_var("outline-variant"))))):
            with me.box(style=me.Style(max_width="900px", margin=me.Margin.symmetric(horizontal="auto"), display="flex", align_items="flex-end", gap=8)):
                me.native_textarea(
                    key="chat_input",
                    on_blur=on_input,
                    placeholder="输入您的问题 (Shift+Enter 发送)...",
                    autosize=True,
                    min_rows=1,
                    max_rows=5,
                    shortcuts={
                        me.Shortcut(shift=True, key="Enter"): on_submit_shortcut,
                    },
                    style=me.Style(flex_grow=1, width="100%"),
                )
                with me.content_button(
                    on_click=on_submit_click,
                    disabled=not state.user_input.strip() or not state.ollama_connected,
                    type="icon",
                    style=me.Style(flex_shrink=0)
                ):
                    me.icon("send")