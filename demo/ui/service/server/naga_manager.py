# -*- coding: utf-8 -*-
import asyncio
import datetime
import json
import os
import uuid
from typing import Optional, List, Any, Tuple

from a2a.types import AgentCard, Artifact, Message, Part, Role, Task, TaskState, TaskStatus, TextPart
from service.server.application_manager import ApplicationManager
from service.types import Conversation, Event

# 优先使用我们新的 /api/chat 轻量适配器；没有就回退到原仓库的 NagaConversation
try:
    from service.naga_conversation import NagaConversation  # 新适配器（调用 FastAPI /api/chat）
    _USE_NEW_ADAPTER = True
except Exception:
    from naga_core.system.conversation_core import NagaConversation  # 原实现（可能是流式）  # type: ignore
    _USE_NEW_ADAPTER = False


def _message_text(m: Message) -> str:
    """把 Message.parts 里的多种 Part 合并成纯文本（给 LLM 请求用）"""
    out: List[str] = []
    for p in m.parts:
        r = p.root
        if r.kind == "text":
            out.append(r.text or "")
        elif r.kind == "data":
            try:
                out.append(json.dumps(r.data, ensure_ascii=False))
            except Exception:
                out.append("<data>")
        elif r.kind == "file":
            out.append(f"[file {getattr(r.file, 'mimeType', '')}]")
    return "\n".join([x for x in out if x])


def _to_text(val: Any) -> str:
    """
    把多种返回（str / dict / list / tuple / None）统一提取为文本：
    - 新适配器：通常是 str 或 {"result": "..."}
    - 旧流式：可能是 (event, token) / {"message":{"content":...}} / 直接 token str
    """
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        if "result" in val and isinstance(val["result"], str):
            return val["result"]
        msg = val.get("message")
        if isinstance(msg, dict):
            c = msg.get("content")
            if isinstance(c, str):
                return c
        # 容错：直接把 dict 序列化
        try:
            return json.dumps(val, ensure_ascii=False)
        except Exception:
            return str(val)
    if isinstance(val, (list, tuple)) and val:
        second = val[1] if len(val) > 1 else val[0]
        return _to_text(second)
    return str(val)


def _split_text_and_image_markdown(md: str) -> Tuple[str, Optional[str]]:
    """
    如果回复里带有 Markdown 图片，拆成“文本 + 图片占位”
    规则：遇到第一次 '![', 之前是文本，之后（含 '![）整体当作图片占位文本
    """
    if not md:
        return "", None
    idx = md.find("![")
    if idx == -1:
        return md.strip(), None
    return md[:idx].strip(), md[idx:].strip()


class NagaManager(ApplicationManager):
    def __init__(self):
        self._conversations: list[Conversation] = []
        self._messages: list[Message] = []
        self._tasks: list[Task] = []
        self._events: list[Event] = []
        self._pending_message_ids: list[str] = []
        # ✅ 修复：task 映射应该是 dict，不是 list
        self._task_map: dict[str, str] = {}
        self._agents: list[AgentCard] = []

        # ---- 构造 NagaConversation（新适配器优先，给足超时） ----
        if _USE_NEW_ADAPTER:
            host = os.getenv("A2A_UI_HOST", "127.0.0.1")
            port = os.getenv("A2A_UI_PORT", "12000")
            base = f"http://{host}:{port}"
            use_mcp_env = str(os.getenv("USE_MCP", "false")).lower() in ("1", "true", "yes")

            self._naga = NagaConversation(
                base_url=base,
                profile="naga",
                use_mcp=use_mcp_env,
                force_report=False,  # 由适配器按关键词自动触发
                timeout=1200,        # ✅ 关键：超时足够等待 ReportEngine 同步执行
            )
        else:
            # 旧实现（流式）；没有自带 /api/chat 的逻辑
            self._naga = NagaConversation()

    # ---- 对话生命周期 -----------------------------------------------------

    def create_conversation(self) -> Conversation:
        c = Conversation(conversation_id=str(uuid.uuid4()), is_active=True)
        self._conversations.append(c)
        return c

    def sanitize_message(self, message: Message) -> Message:
        conv = self.get_conversation(message.contextId)
        if not conv or not conv.messages:
            return message
        last = conv.messages[-1]
        # 如果上一个 task 还没完成，就把当前消息挂到那个 task 上
        if last.taskId and any(
            t for t in self._tasks if t and t.id == last.taskId and t.status.state != TaskState.completed
        ):
            message.taskId = last.taskId
        return message

    async def process_message(self, message: Message):
        # 记录用户消息
        self._messages.append(message)
        if message.messageId:
            self._pending_message_ids.append(message.messageId)
        conv = self.get_conversation(message.contextId)
        if conv:
            conv.messages.append(message)
        self._events.append(
            Event(id=str(uuid.uuid4()), actor="user", content=message, timestamp=datetime.datetime.utcnow().timestamp())
        )

        # 任务登记
        task_id = message.taskId or str(uuid.uuid4())
        current_task = next((t for t in self._tasks if t.id == task_id), None)
        if not current_task:
            current_task = Task(
                id=task_id,
                status=TaskStatus(state=TaskState.submitted, message=message),
                artifacts=[],
                contextId=message.contextId,
            )
            self._tasks.append(current_task)
        if message.messageId:
            # ✅ 修复：使用 dict 赋值，而不是 append
            self._task_map[message.messageId] = task_id

        # ---- 调 Naga 得到回复（兼容多种返回形式） ----
        reply_text = ""
        try:
            user_text = _message_text(message)

            # 新适配器：可能是“异步流式生成器”接口
            if hasattr(self._naga, "process_with_tool_results"):
                ret = self._naga.process_with_tool_results(user_text)
                if hasattr(ret, "__aiter__"):
                    chunks: List[str] = []
                    async for piece in ret:  # piece 可能是 str / (event, token) / dict / ...
                        chunks.append(_to_text(piece))
                    reply_text = "".join(chunks)
                else:
                    if asyncio.iscoroutine(ret):
                        val = await ret
                    else:
                        val = ret
                    reply_text = _to_text(val)

            # 也可能只有 chat/achat
            elif hasattr(self._naga, "achat"):
                reply_text = _to_text(await self._naga.achat(user_text))
            elif hasattr(self._naga, "chat"):
                loop = asyncio.get_running_loop()
                reply_text = _to_text(await loop.run_in_executor(None, lambda: self._naga.chat(user_text)))
            else:
                reply_text = "[Naga Error] adapter has no supported chat method"

        except Exception as e:
            reply_text = f"[Naga Error] {e}"

        # ---- 生成回复 Message（文本 + 可选图片占位） ----
        text_part, image_md = _split_text_and_image_markdown(reply_text)
        reply_parts: List[Part] = []
        if text_part:
            reply_parts.append(Part(root=TextPart(text=text_part)))
        if image_md:
            reply_parts.append(Part(root=TextPart(text=image_md)))

        reply = Message(
            role=Role.agent,
            parts=reply_parts if reply_parts else [Part(root=TextPart(text=""))],
            contextId=message.contextId,
            taskId=task_id,
            messageId=str(uuid.uuid4()),
        )

        self._messages.append(reply)
        if conv:
            conv.messages.append(reply)

        # 更新事件 & 任务状态
        self._events.append(
            Event(id=str(uuid.uuid4()), actor="naga", content=reply, timestamp=datetime.datetime.utcnow().timestamp())
        )
        current_task.status.state = TaskState.completed
        current_task.status.message = reply
        current_task.artifacts = [Artifact(name="response", parts=reply.parts, artifactId=str(uuid.uuid4()))]

        if message.messageId in self._pending_message_ids:
            self._pending_message_ids.remove(message.messageId)

    # ---- 其它管理接口 -----------------------------------------------------

    def register_agent(self, url: str):
        from utils.agent_card import get_agent_card
        card = get_agent_card(url)
        if not card.url:
            card.url = url
        self._agents.append(card)

    def get_pending_messages(self) -> list[tuple[str, str]]:
        # 在 _task_map（dict）中存在即视为“正在处理”
        return [(mid, "Working..." if mid in self._task_map else "") for mid in self._pending_message_ids]

    def get_conversation(self, conversation_id: Optional[str]) -> Optional[Conversation]:
        if not conversation_id:
            return None
        return next((c for c in self._conversations if c and c.conversation_id == conversation_id), None)

    @property
    def conversations(self) -> list[Conversation]:
        return self._conversations

    @property
    def messages(self) -> list[Message]:
        return self._messages

    @property
    def tasks(self) -> list[Task]:
        return self._tasks

    @property
    def events(self) -> list[Event]:
        return self._events

    @property
    def agents(self) -> list[AgentCard]:
        return self._agents
