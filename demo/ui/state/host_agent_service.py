# state/host_agent_service.py —— pending + 表单识别 版本（覆盖现有文件）

import json
import os
import traceback
import uuid
from typing import Any, Optional, List, Tuple

from a2a.types import FileWithBytes, Message, Part, Role, Task, TaskState, TextPart
from service.client.client import ConversationClient
from service.types import (
    Conversation,
    CreateConversationRequest,
    Event,
    GetEventRequest,
    ListAgentRequest,
    ListConversationRequest,
    ListMessageRequest,
    ListTaskRequest,
    MessageInfo,
    PendingMessageRequest,
    RegisterAgentRequest,
    SendMessageRequest,
)

from .state import (
    AppState,
    SessionTask,
    StateConversation,
    StateEvent,
    StateMessage,
    StateTask,
)

SERVER_URL = os.getenv("A2A_UI_BASE", "http://127.0.0.1:12000").rstrip("/")
_client = ConversationClient(SERVER_URL)

# ---------------------------
# 核心：会话 + 发送 + 等待完成
# ---------------------------
async def ensure_conversation_id(current_id: Optional[str]) -> str:
    if current_id:
        return current_id
    resp = await _client.create_conversation(CreateConversationRequest(method="conversation/create", params={}))
    return resp.result.conversation_id

async def send_user_text(context_id: str, text: str) -> Tuple[str, str]:
    msg = Message(
        role=Role.user,
        messageId=str(uuid.uuid4()),
        contextId=context_id,
        parts=[Part(root=TextPart(text=text))],
    )
    resp = await _client.send_message(SendMessageRequest(method="message/send", params=msg))
    result: MessageInfo = resp.result
    return result.message_id, result.context_id

async def wait_by_pending(message_id: str, context_id: str, timeout_s: float = 45.0, poll_interval: float = 0.6) -> None:
    """仅等待完成，不返回文本。文本/表单请随后用 get_last_agent_reply 拉取。"""
    import asyncio
    elapsed = 0.0
    while elapsed < timeout_s:
        pend = await _client.get_pending_messages(PendingMessageRequest(method="message/pending", params={}))
        pending_map = dict(pend.result or [])
        if message_id not in pending_map:
            return
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return

async def get_last_agent_reply(context_id: str) -> Tuple[str, Any]:
    """返回 ('form', form_dict) 或 ('text', text_str)；若没有则 ('none','')."""
    lm = await _client.list_messages(ListMessageRequest(method="message/list", params=context_id))
    msgs: List[Message] = lm.result or []
    for m in reversed(msgs):
        if m.role == Role.agent:
            # 优先识别 data/form
            for p in m.parts or []:
                r = p.root
                if r.kind == 'data':
                    try:
                        data = r.data
                        if isinstance(data, dict) and data.get('type') == 'form':
                            return ('form', data)
                    except Exception:
                        pass
            # 其次拼接文本
            return ('text', _parts_to_text(m.parts))
    return ('none', '')

def _parts_to_text(parts: List[Part]) -> str:
    out: List[str] = []
    for p in parts or []:
        r = p.root
        if r.kind == "text":
            out.append(r.text or "")
        elif r.kind == "data":
            # 非表单的数据转成 json 字符串
            try:
                if isinstance(r.data, dict) and r.data.get('type') == 'form':
                    # 这里不直接转文本，由上层 get_last_agent_reply 处理
                    continue
                out.append(json.dumps(r.data, ensure_ascii=False))
            except Exception:
                out.append("<data>")
        elif r.kind == "file":
            out.append(f"[file {getattr(r.file,'mimeType','')}]")
    return "\n".join([x for x in out if x])

# ---------------------------
# 其余：保持你原有工具函数（加回对 form 的识别）
# ---------------------------
async def ListConversations() -> list[Conversation]:
    response = await _client.list_conversation(ListConversationRequest(method="conversation/list", params={}))
    return response.result if response.result else []

async def SendMessage(message: Message) -> Message | MessageInfo | None:
    try:
        response = await _client.send_message(SendMessageRequest(method="message/send", params=message))
        return response.result
    except Exception as e:
        traceback.print_exc()
        print("Failed to send message: ", e)
    return None

async def CreateConversation() -> Conversation:
    try:
        response = await _client.create_conversation(CreateConversationRequest(method="conversation/create", params={}))
        return response.result if response.result else Conversation(conversation_id="", is_active=False)
    except Exception as e:
        print("Failed to create conversation", e)
    return Conversation(conversation_id="", is_active=False)

async def UpdateAppState(state: AppState, conversation_id: str):
    try:
        if conversation_id:
            state.current_conversation_id = conversation_id
            messages = await ListMessages(conversation_id)
            state.legacy_messages = [] if not messages else [convert_message_to_state(x) for x in messages]

        conversations = await ListConversations()
        state.conversations = [] if not conversations else [convert_conversation_to_state(x) for x in conversations]

        tasks = await GetTasks()
        state.task_list = ([SessionTask(context_id=extract_conversation_id(t), task=convert_task_to_state(t)) for t in tasks] if tasks else [])

    except Exception as e:
        print("Failed to update state: ", e)
        traceback.print_exc()

async def ListRemoteAgents():
    response = await _client.list_agents(ListAgentRequest(method="agent/list", params={}))
    return response.result

async def AddRemoteAgent(path: str):
    await _client.register_agent(RegisterAgentRequest(method="agent/register", params=path))

async def GetEvents() -> list[Event]:
    response = await _client.get_events(GetEventRequest(method="events/get", params={}))
    return response.result if response.result else []

async def GetProcessingMessages():
    response = await _client.get_pending_messages(PendingMessageRequest(method="message/pending", params={}))
    return dict(response.result) if response.result else {}

def GetMessageAliases(): return {}

async def GetTasks():
    response = await _client.list_tasks(ListTaskRequest(method="task/list", params={}))
    return response.result if response.result else []

async def ListMessages(conversation_id: str) -> list[Message]:
    response = await _client.list_messages(ListMessageRequest(method="message/list", params=conversation_id))
    return response.result if response.result else []

async def UpdateApiKey(api_key: str):
    import httpx
    try:
        os.environ["GOOGLE_API_KEY"] = api_key
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{SERVER_URL}/api_key/update", json={"api_key": api_key})
            response.raise_for_status()
        return True
    except Exception as e:
        print("Failed to update API key: ", e)
        return False

# ------- 状态转换：识别 form -------
def convert_message_to_state(message: Message) -> StateMessage:
    if not message: return StateMessage()
    return StateMessage(
        message_id=message.messageId,
        context_id=message.contextId if message.contextId else "",
        task_id=message.taskId if message.taskId else "",
        role=message.role.name,
        content=extract_content(message.parts),
    )

def convert_conversation_to_state(conversation: Conversation) -> StateConversation:
    return StateConversation(
        conversation_id=conversation.conversation_id,
        conversation_name=conversation.name,
        is_active=conversation.is_active,
        message_ids=[extract_message_id(x) for x in conversation.messages],
    )

def convert_task_to_state(task: Task) -> StateTask:
    output = ([extract_content(a.parts) for a in task.artifacts] if task.artifacts else [])
    if not task.history:
        return StateTask(task_id=task.id, context_id=task.contextId, state=TaskState.failed.name, message=StateMessage(), artifacts=output)
    message = task.history[0]
    last_message = task.history[-1]
    if last_message != message:
        output = [extract_content(last_message.parts)] + output
    return StateTask(task_id=task.id, context_id=task.contextId, state=str(task.status.state), message=convert_message_to_state(message), artifacts=output)

def convert_event_to_state(event: Event) -> StateEvent:
    return StateEvent(context_id=extract_message_conversation(event.content), actor=event.actor, role=event.content.role.name, id=event.id, content=extract_content(event.content.parts))

def extract_content(message_parts: list[Part]) -> list[tuple[str | dict[str, Any], str]]:
    parts: list[tuple[str | dict[str, Any], str]] = []
    if not message_parts: return []
    for part in message_parts:
        p = part.root
        if p.kind == "text":
            parts.append((p.text, "text/plain"))
        elif p.kind == "file":
            if isinstance(p.file, FileWithBytes): parts.append((p.file.bytes, p.file.mimeType or ""))
            else: parts.append((p.file.uri, p.file.mimeType or ""))
        elif p.kind == "data":
            try:
                if isinstance(p.data, dict) and p.data.get("type") == "form":
                    # ✅ 关键：标记为 'form'，页面可用 render_form 渲染
                    parts.append((p.data, "form"))
                else:
                    parts.append((json.dumps(p.data, ensure_ascii=False), "application/json"))
            except Exception:
                parts.append(("<data>", "text/plain"))
    return parts

def extract_message_id(message: Message) -> str: return message.messageId
def extract_message_conversation(message: Message) -> str: return message.contextId if message.contextId else ""
def extract_conversation_id(task: Task) -> str:
    if task.contextId: return task.contextId
    if task.status.message: return task.status.message.contextId or ""
    return ""
