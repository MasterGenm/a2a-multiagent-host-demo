# state/host_agent_service.py (最终修正版 - 解决数据污染问题)

import json
import os
import sys
import traceback
import uuid
from typing import Any

from a2a.types import FileWithBytes, Message, Part, Role, Task, TaskState
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

# 导入所有需要的状态类型
from .state import (
    AppState,
    SessionTask,
    StateConversation,
    StateEvent,
    StateMessage,
    StateTask,
)

server_url = 'http://localhost:12000'


async def ListConversations() -> list[Conversation]:
    client = ConversationClient(server_url)
    try:
        response = await client.list_conversation(ListConversationRequest())
        return response.result if response.result else []
    except Exception as e:
        print('Failed to list conversations: ', e)
    return []


async def SendMessage(message: Message) -> Message | MessageInfo | None:
    client = ConversationClient(server_url)
    try:
        response = await client.send_message(SendMessageRequest(params=message))
        return response.result
    except Exception as e:
        traceback.print_exc()
        print('Failed to send message: ', e)
    return None


async def CreateConversation() -> Conversation:
    client = ConversationClient(server_url)
    try:
        response = await client.create_conversation(CreateConversationRequest())
        return (
            response.result
            if response.result
            else Conversation(conversation_id='', is_active=False)
        )
    except Exception as e:
        print('Failed to create conversation', e)
    return Conversation(conversation_id='', is_active=False)


async def UpdateAppState(state: AppState, conversation_id: str):
    """
    更新应用状态（修正版）。
    这个函数现在只更新与旧版 a2a 框架相关的状态字段。
    """
    try:
        if conversation_id:
            state.current_conversation_id = conversation_id
            messages = await ListMessages(conversation_id)
            
            # ==========================================================
            # 关键修复：将旧格式的消息放入 legacy_messages 列表，
            # 而不是污染新的 state.messages 列表。
            # ==========================================================
            if not messages:
                state.legacy_messages = []
            else:
                state.legacy_messages = [convert_message_to_state(x) for x in messages]

        conversations = await ListConversations()
        if not conversations:
            state.conversations = []
        else:
            state.conversations = [
                convert_conversation_to_state(x) for x in conversations
            ]

        tasks = await GetTasks()
        if tasks:
            state.task_list = [
                SessionTask(
                    context_id=extract_conversation_id(task),
                    task=convert_task_to_state(task),
                )
                for task in tasks
            ]
        else:
            state.task_list = []
            
    except Exception as e:
        print('Failed to update state: ', e)
        traceback.print_exc(file=sys.stdout)


# --------------------------------------------------------------------------
#  下面的所有辅助函数保持不变，因为它们是 a2a 框架的内部逻辑
# --------------------------------------------------------------------------

async def ListRemoteAgents():
    client = ConversationClient(server_url)
    try:
        response = await client.list_agents(ListAgentRequest())
        return response.result
    except Exception as e:
        print('Failed to read agents', e)

async def AddRemoteAgent(path: str):
    client = ConversationClient(server_url)
    try:
        await client.register_agent(RegisterAgentRequest(params=path))
    except Exception as e:
        print('Failed to register the agent', e)

async def GetEvents() -> list[Event]:
    client = ConversationClient(server_url)
    try:
        response = await client.get_events(GetEventRequest())
        return response.result if response.result else []
    except Exception as e:
        print('Failed to get events', e)
    return []

async def GetProcessingMessages():
    client = ConversationClient(server_url)
    try:
        response = await client.get_pending_messages(PendingMessageRequest())
        return dict(response.result) if response.result else {}
    except Exception as e:
        print('Error getting pending messages', e)
        return {}

def GetMessageAliases():
    return {}

async def GetTasks():
    client = ConversationClient(server_url)
    try:
        response = await client.list_tasks(ListTaskRequest())
        return response.result if response.result else []
    except Exception as e:
        print('Failed to list tasks ', e)
        return []

async def ListMessages(conversation_id: str) -> list[Message]:
    client = ConversationClient(server_url)
    try:
        response = await client.list_messages(
            ListMessageRequest(params=conversation_id)
        )
        return response.result if response.result else []
    except Exception as e:
        print('Failed to list messages ', e)
    return []

async def UpdateApiKey(api_key: str):
    import httpx
    try:
        os.environ['GOOGLE_API_KEY'] = api_key
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f'{server_url}/api_key/update', json={'api_key': api_key}
            )
            response.raise_for_status()
        return True
    except Exception as e:
        print('Failed to update API key: ', e)
        return False

def convert_message_to_state(message: Message) -> StateMessage:
    if not message: return StateMessage()
    return StateMessage(
        message_id=message.messageId,
        context_id=message.contextId if message.contextId else '',
        task_id=message.taskId if message.taskId else '',
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
        return StateTask(task_id=task.id, context_id=task.contextId, state=TaskState.failed.name, message=StateMessage(message_id=str(uuid.uuid4()), context_id=task.contextId, task_id=task.id, role=Role.agent.name, content=[('No history', 'text')]), artifacts=output)
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
        if p.kind == 'text': parts.append((p.text, 'text/plain'))
        elif p.kind == 'file':
            if isinstance(p.file, FileWithBytes): parts.append((p.file.bytes, p.file.mimeType or ''))
            else: parts.append((p.file.uri, p.file.mimeType or ''))
        elif p.kind == 'data':
            try:
                jsonData = json.dumps(p.data)
                if 'type' in p.data and p.data['type'] == 'form': parts.append((p.data, 'form'))
                else: parts.append((jsonData, 'application/json'))
            except Exception as e:
                print('Failed to dump data', e)
                parts.append(('<data>', 'text/plain'))
    return parts

def extract_message_id(message: Message) -> str: return message.messageId
def extract_message_conversation(message: Message) -> str: return message.contextId if message.contextId else ''
def extract_conversation_id(task: Task) -> str:
    if task.contextId: return task.contextId
    if task.status.message: return task.status.message.contextId or ''
    return ''