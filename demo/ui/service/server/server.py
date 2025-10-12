# service/server/server.py —— NAGA/ADK/内存 后端统一路由 + 直达MCP通道（自适应 execute_tool_calls 签名 & 修复 role=agent）
import asyncio
import base64
import inspect
import os
import threading
import uuid
from typing import Callable, List

import httpx
from fastapi import FastAPI, Request, Response

# ---- A2A & 本项目类型 ----
from a2a.types import FilePart, FileWithUri, Message, Part, TextPart
from service.types import (
    CreateConversationResponse,
    GetEventResponse,
    ListAgentResponse,
    ListConversationResponse,
    ListMessageResponse,
    ListTaskResponse,
    MessageInfo,
    PendingMessageResponse,
    RegisterAgentResponse,
    SendMessageResponse,
)
from .application_manager import ApplicationManager
from .in_memory_manager import InMemoryFakeAgentManager

# ---- MCP 直达通道所需 ----
from mcpserver.tool_call_utils import parse_tool_calls, execute_tool_calls  # 可能是老/新版本
from mcpserver import mcp_manager as mcp_manager_module

try:
    from mcpserver.mcp_registry import refresh_registry
    refresh_registry()
except ImportError:
    # 如果无法导入refresh_registry，则尝试其他方式初始化MCP服务
    try:
        from mcpserver.mcp_registry import auto_register_mcp
        auto_register_mcp()
    except ImportError:
        # 最后的兜底方案
        try:
            from mcp_registry import auto_register_mcp
            auto_register_mcp()
        except Exception:
            pass


class ConversationServer:
    """统一注册 Mesop UI 需要的后端接口，后端实现由 A2A_HOST 决定：ADK / NAGA / InMemory。"""

    def __init__(self, app: FastAPI, http_client: httpx.AsyncClient):
        agent_backend = os.environ.get("A2A_HOST", "ADK").upper()

        # 默认：内存后端
        self.manager: ApplicationManager = InMemoryFakeAgentManager()
        self._get_message_id: Callable[[Message], str] = lambda m: (
            m.messageId if m.messageId else f"m-{uuid.uuid4()}"
        )

        if agent_backend == "ADK":
            try:
                from .adk_host_manager import ADKHostManager, get_message_id as adk_get_message_id  # type: ignore
                api_key = os.environ.get("GOOGLE_API_KEY", "")
                uses_vertex_ai = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").upper() == "TRUE"
                self.manager = ADKHostManager(http_client, api_key=api_key, uses_vertex_ai=uses_vertex_ai)
                self._get_message_id = adk_get_message_id
                print("[server] Using ADKHostManager backend")
            except Exception as e:
                print(f"[server] WARN: ADK backend requested but failed to import ({e}); falling back to InMemory.")

        elif agent_backend == "NAGA":
            try:
                from .naga_manager import NagaManager  # 延迟导入
                self.manager = NagaManager()
                print("[server] Using NagaManager backend")
            except Exception as e:
                print(f"[server] ERROR: NagaManager import failed ({e}); falling back to InMemory.")
        else:
            print("[server] Using InMemoryFakeAgentManager backend")

        # 文件缓存：将消息里的 FilePart 转换为可下载 URI
        self._file_cache: dict[str, FilePart] = {}
        self._message_to_cache: dict[str, str] = {}

        # 路由注册
        app.add_api_route("/conversation/create", self._create_conversation, methods=["POST"])
        app.add_api_route("/conversation/list", self._list_conversation, methods=["POST"])
        app.add_api_route("/message/send", self._send_message, methods=["POST"])
        app.add_api_route("/events/get", self._get_events, methods=["POST"])
        app.add_api_route("/message/list", self._list_messages, methods=["POST"])
        app.add_api_route("/message/pending", self._pending_messages, methods=["POST"])
        app.add_api_route("/task/list", self._list_tasks, methods=["POST"])
        app.add_api_route("/agent/register", self._register_agent, methods=["POST"])
        app.add_api_route("/agent/list", self._list_agents, methods=["POST"])
        app.add_api_route("/message/file/{file_id}", self._files, methods=["GET"])
        app.add_api_route("/api_key/update", self._update_api_key, methods=["POST"])

    # ---------- 会话 ----------
    async def _create_conversation(self):
        maybe = self.manager.create_conversation()
        c = await maybe if inspect.isawaitable(maybe) else maybe
        return CreateConversationResponse(result=c)

    async def _list_conversation(self):
        return ListConversationResponse(result=self.manager.conversations)

    # ---------- 工具：从 Message 中尽力抽出纯文本 ----------
    def _extract_plain_text(self, msg: Message) -> str:
        txt = getattr(msg, "text", None)
        if isinstance(txt, str) and txt.strip():
            return txt
        out: List[str] = []
        try:
            for p in msg.parts or []:
                root = getattr(p, "root", None)
                if root is None:
                    continue
                for key in ("text", "content", "value"):
                    val = getattr(root, key, None)
                    if isinstance(val, str) and val.strip():
                        out.append(val)
        except Exception:
            pass
        return "\n".join(out).strip()

    # ---------- 兼容老/新版本 execute_tool_calls ----------
    async def _run_execute(self, tool_calls: list) -> str:
        """
        自适应：
        - 新版: async def execute_tool_calls(tool_calls, mcp_manager) -> str
        - 老版: async def execute_tool_calls(tool_calls) -> str
        - 以及可能的同步定义
        """
        try:
            sig = inspect.signature(execute_tool_calls)
            params = list(sig.parameters.values())
            is_coro = inspect.iscoroutinefunction(execute_tool_calls)

            async def _call(*args):
                if is_coro:
                    return await execute_tool_calls(*args)
                # 同步函数也包一层，接口一致
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, lambda: execute_tool_calls(*args))

            if len(params) >= 2:
                # 需要 mcp_manager
                return await _call(tool_calls, mcp_manager_module)
            else:
                # 只要 tool_calls
                return await _call(tool_calls)
        except TypeError:
            # 兜底：先试 (calls)，失败再试 (calls, mcp_manager)
            try:
                if inspect.iscoroutinefunction(execute_tool_calls):
                    return await execute_tool_calls(tool_calls)
                else:
                    loop = asyncio.get_event_loop()
                    return await loop.run_in_executor(None, lambda: execute_tool_calls(tool_calls))
            except Exception:
                if inspect.iscoroutinefunction(execute_tool_calls):
                    return await execute_tool_calls(tool_calls, mcp_manager_module)
                else:
                    loop = asyncio.get_event_loop()
                    return await loop.run_in_executor(None, lambda: execute_tool_calls(tool_calls, mcp_manager_module))

    # ---------- 消息 ----------
    async def _send_message(self, request: Request):
        payload = await request.json()
        params = payload.get("params", {})
        message = Message(**params)
        message = self.manager.sanitize_message(message)

        # ===== 直达 MCP 通道：如果用户这条消息本身就是合法工具 JSON，直接执行并落库 =====
        user_text = ""
        try:
            user_text = params.get("text") or params.get("content") or ""
        except Exception:
            pass
        if not user_text:
            user_text = self._extract_plain_text(message)

        direct_calls = []
        try:
            direct_calls = parse_tool_calls(user_text)
        except Exception:
            direct_calls = []

        if direct_calls:
            # 1) 先把“用户消息”落库（UI可见）
            try:
                conv_id = message.contextId or ""
                conv = self.manager.get_conversation(conv_id)
                if conv is not None:
                    if not getattr(message, "messageId", None):
                        message.messageId = f"m-{uuid.uuid4()}"
                    conv.messages.append(message)
            except Exception as e:
                print(f"[server] WARN: append user message failed in MCP-direct path: {e}")

            # 2) 执行 MCP（自适应老/新签名）
            try:
                tool_result = await self._run_execute(direct_calls)
            except Exception as e:
                tool_result = f"工具调用解析/执行出错：{e}"

            # 3) 把工具结果作为“agent”消息落库（✅ role 必须是 'agent'）
            try:
                conv_id = message.contextId or ""
                conv = self.manager.get_conversation(conv_id)
                if conv is not None:
                    text_out = f"工具调用结果：\n{tool_result}"
                    agent_msg = Message(
                        messageId=f"m-{uuid.uuid4()}",
                        contextId=conv_id,
                        role="agent",
                        text=text_out,
                        parts=[Part(root=TextPart(text=text_out))],
                    )
                    conv.messages.append(agent_msg)
            except Exception as e:
                print(f"[server] WARN: append agent message failed in MCP-direct path: {e}")

            # 4) 返回 ack（UI 会通过 /message/list 拉取）
            return SendMessageResponse(
                result=MessageInfo(
                    message_id=message.messageId,
                    context_id=message.contextId or "",
                )
            )

        # ===== 常规路径：交给后台管线（ADK/NAGA/InMemory）去处理 =====
        loop = asyncio.get_event_loop()
        if hasattr(self.manager, "process_message_threadsafe"):
            t = threading.Thread(target=lambda: getattr(self.manager, "process_message_threadsafe")(message, loop))
        else:
            t = threading.Thread(target=lambda: asyncio.run(self.manager.process_message(message)))
        t.start()

        return SendMessageResponse(
            result=MessageInfo(
                message_id=message.messageId,
                context_id=message.contextId if message.contextId else "",
            )
        )

    async def _list_messages(self, request: Request):
        data = await request.json()
        conversation_id = data["params"]
        conversation = self.manager.get_conversation(conversation_id)
        if conversation:
            return ListMessageResponse(result=self._cache_content(conversation.messages))
        return ListMessageResponse(result=[])

    # ---------- 事件 / 任务 / Agent ----------
    async def _get_events(self, request: Request):
        return GetEventResponse(result=self.manager.events)

    async def _pending_messages(self):
        return PendingMessageResponse(result=self.manager.get_pending_messages())

    def _list_tasks(self):
        return ListTaskResponse(result=self.manager.tasks)

    async def _register_agent(self, request: Request):
        data = await request.json()
        url = data["params"]
        self.manager.register_agent(url)
        return RegisterAgentResponse()

    async def _list_agents(self):
        return ListAgentResponse(result=self.manager.agents)

    # ---------- 文件 ----------
    def _cache_content(self, messages: list[Message]):
        rval: list[Message] = []
        for m in messages:
            mid = self._get_message_id(m)
            if not m.messageId:
                m.messageId = mid

            new_parts: list[Part] = []
            for i, p in enumerate(m.parts):
                part = p.root
                if getattr(part, "kind", "") != "file":
                    new_parts.append(p)
                    continue

                message_part_id = f"{mid}:{i}"
                cache_id = self._message_to_cache.get(message_part_id) or str(uuid.uuid4())
                self._message_to_cache[message_part_id] = cache_id

                new_parts.append(
                    Part(
                        root=FilePart(
                            file=FileWithUri(
                                mimeType=part.file.mimeType,
                                uri=f"/message/file/{cache_id}",
                            )
                        )
                    )
                )
                if cache_id not in self._file_cache:
                    self._file_cache[cache_id] = part

            m.parts = new_parts
            rval.append(m)
        return rval

    def _files(self, file_id: str):
        if file_id not in self._file_cache:
            raise Exception("file not found")
        part = self._file_cache[file_id]
        if "image" in part.file.mimeType:
            return Response(content=base64.b64decode(part.file.bytes), media_type=part.file.mimeType)
        return Response(content=part.file.bytes, media_type=part.file.mimeType)

    # ---------- 仅 ADK：在线改 key（其它后端忽略） ----------
    async def _update_api_key(self, request: Request):
        try:
            data = await request.json()
            api_key = data.get("api_key", "")
            if api_key and hasattr(self.manager, "update_api_key"):
                getattr(self.manager, "update_api_key")(api_key)
                return {"status": "success"}
            elif not api_key:
                return {"status": "error", "message": "No API key provided"}
            else:
                return {"status": "ignored", "message": "Backend does not support update_api_key"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
