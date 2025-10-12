# -*- coding: utf-8 -*-
"""
remote_mcp_proxy.py — 远程 MCP 的「HTTP 首选 + httpx 兜底」版
- 完全绕开 SSE
- 优先使用 mcp.client.streamable_http + ClientSession（多版本签名自适配）
- 失败则自动降级 httpx 直连（兼容 DashScope / 任意 streamable_http MCP）
- 支持 __list_tools 与任意工具直调（/invocations、/tools/{name}/invoke、/tools/{name}）
"""
from __future__ import annotations
import json
import asyncio
import importlib
import inspect
from typing import Any, Dict, Optional, List, Tuple

import httpx


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def _extract_text_from_mcp_content(content: Any) -> str:
    """尽量把 MCP 的 content 提取为文本（兼容 text/data/列表）。"""
    if content is None:
        return ""
    if isinstance(content, dict):
        if "text" in content and isinstance(content["text"], str):
            return content["text"]
        if "data" in content:
            try:
                return _safe_json(content["data"])
            except Exception:
                pass
        if "content" in content:  # 某些实现外层还有一层 content
            return _extract_text_from_mcp_content(content["content"])
    if isinstance(content, list):
        parts: List[str] = []
        for c in content:
            if isinstance(c, dict) and "text" in c and isinstance(c["text"], str):
                parts.append(c["text"])
            elif isinstance(c, dict) and "data" in c:
                parts.append(_safe_json(c["data"]))
            else:
                parts.append(_safe_json(c))
        return "\n".join(parts)
    return _safe_json(content)


class RemoteMCPProxy:
    def __init__(
        self,
        name: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        transport: str = "streamable_http",
        display_name: Optional[str] = None,
        description: str = "",
        is_active: bool = True,
    ) -> None:
        self.name = name
        self.base_url = url.rstrip("/")
        self.headers = headers or {}
        self.transport = (transport or "streamable_http").lower()
        self.display_name = display_name or name
        self.description = description
        self.is_active = is_active

        self._stack = self._detect_http_stack()  # {'HTTPTransport': class|None, 'ClientSession': class|None}

    # ---------------------------
    # 能力探测 & 构造
    # ---------------------------
    def _detect_http_stack(self) -> Dict[str, Any]:
        out = {"HTTPTransport": None, "ClientSession": None}
        try:
            mod = importlib.import_module("mcp.client.streamable_http")
            # 兼容不同版本的命名
            for cand in ("StreamableHTTPTransport", "HTTPTransport", "StreamableHTTPClientTransport"):
                cls = getattr(mod, cand, None)
                if cls:
                    out["HTTPTransport"] = cls
                    break
        except Exception:
            pass
        try:
            sess_mod = importlib.import_module("mcp.client.session")
            out["ClientSession"] = getattr(sess_mod, "ClientSession", None)
        except Exception:
            pass
        return out

    def _make_transport(self):
        T = self._stack.get("HTTPTransport")
        if not T:
            raise RuntimeError("无可用 HTTP Transport 类")
        # 智能匹配构造参数
        kwargs = {}
        sig = None
        try:
            sig = inspect.signature(T)
        except Exception:
            pass
        if sig:
            params = set(sig.parameters.keys())
            if "url" in params:
                kwargs["url"] = self.base_url
            elif "base_url" in params:
                kwargs["base_url"] = self.base_url
            elif "endpoint" in params:
                kwargs["endpoint"] = self.base_url
            # headers 常见
            if "headers" in params:
                kwargs["headers"] = self.headers
        # 兜底：尝试常见形式
        try:
            return T(**kwargs)
        except TypeError:
            try:
                return T(self.base_url, self.headers)
            except Exception as e:
                raise RuntimeError(f"构造 Transport 失败: {e}")

    async def _open_session(self):
        """多版本 ClientSession 兼容：构造/连接/上下文管理均做降级尝试。"""
        ClientSession = self._stack.get("ClientSession")
        if not ClientSession:
            raise RuntimeError("无可用 ClientSession 类")
        transport = self._make_transport()

        # 方案 A：with ClientSession(transport=...)
        try:
            sig = inspect.signature(ClientSession)
            if "transport" in sig.parameters:
                session = ClientSession(transport=transport)
                return session, True  # True 表示可作为 async context manager 返回
        except Exception:
            pass

        # 方案 B：先构造，再连接（connect / open）
        session = ClientSession()
        if hasattr(session, "connect"):
            try:
                await session.connect(transport)  # 有的版本是 connect(transport)
                return session, False
            except TypeError:
                try:
                    await session.connect(transport=transport)  # 有的版本是 connect(transport=...)
                    return session, False
                except Exception:
                    pass
        if hasattr(session, "open"):
            try:
                await session.open(transport=transport)
                return session, False
            except TypeError:
                try:
                    await session.open(transport)
                    return session, False
                except Exception:
                    pass

        # 方案 C：with ClientSession() as s，然后 s.connect(...)
        return session, False

    async def _aclose_session(self, session):
        """关闭 session（兼容有无 aclose/close）。"""
        try:
            if hasattr(session, "aclose"):
                await session.aclose()
            elif hasattr(session, "close"):
                res = session.close()
                if inspect.isawaitable(res):
                    await res
        except Exception:
            pass

    # ---------------------------
    # 对外 API
    # ---------------------------
    def invoke(self, tool_name: str, **kwargs) -> str:
        if tool_name == "__list_tools":
            return self.list_tools()

        # 优先官方 HTTP 客户端；失败即刻静默降级 httpx 直连
        if self._stack.get("HTTPTransport") and self._stack.get("ClientSession"):
            try:
                return asyncio.run(self._invoke_via_mcp(tool_name, kwargs))
            except Exception:
                # 静默降级
                pass
        return self._invoke_via_httpx(tool_name, kwargs)

    def list_tools(self) -> str:
        # 优先官方客户端；失败直接给 httpx 结果（不返回错误文本）
        if self._stack.get("HTTPTransport") and self._stack.get("ClientSession"):
            try:
                return asyncio.run(self._list_tools_via_mcp())
            except Exception:
                pass
        return self._list_tools_via_httpx()

    # ---------------------------
    # 官方 HTTP 客户端路径（异步）
    # ---------------------------
    async def _invoke_via_mcp(self, tool_name: str, args: Dict[str, Any]) -> str:
        session, can_ctx = await self._open_session()
        if can_ctx and hasattr(session, "__aenter__"):
            async with session as s:
                # list_tools 不是必须，但能提前验证工具存在
                try:
                    tl = await s.list_tools()
                    _ = [t.name for t in getattr(tl, "tools", [])]
                except Exception:
                    pass
                result = await s.call_tool(name=tool_name, arguments=args)
                return _extract_text_from_mcp_content(result.content)
        else:
            try:
                # 尝试 list_tools（可选）
                try:
                    tl = await session.list_tools()
                    _ = [t.name for t in getattr(tl, "tools", [])]
                except Exception:
                    pass
                result = await session.call_tool(name=tool_name, arguments=args)
                return _extract_text_from_mcp_content(result.content)
            finally:
                await self._aclose_session(session)

    async def _list_tools_via_mcp(self) -> str:
        session, can_ctx = await self._open_session()
        tools: List[Dict[str, Any]] = []
        if can_ctx and hasattr(session, "__aenter__"):
            async with session as s:
                tl = await s.list_tools()
                for t in getattr(tl, "tools", []):
                    tools.append({
                        "name": getattr(t, "name", None),
                        "description": getattr(t, "description", "") or "",
                    })
        else:
            try:
                tl = await session.list_tools()
                for t in getattr(t, "tools", []):
                    tools.append({
                        "name": getattr(t, "name", None),
                        "description": getattr(t, "description", "") or "",
                    })
            finally:
                await self._aclose_session(session)
        return _safe_json({"service": self.name, "tools": tools})

    # ---------------------------
    # httpx 直连路径（同步）
    # ---------------------------
    def _invoke_via_httpx(self, tool_name: str, args: Dict[str, Any]) -> str:
        """按常见三种路由尝试调用。"""
        payloads: List[Tuple[str, Dict[str, Any]]] = [
            (f"{self.base_url}/invocations", {"name": tool_name, "arguments": args}),
            (f"{self.base_url}/tools/{tool_name}/invoke", {"arguments": args}),
            (f"{self.base_url}/tools/{tool_name}", {"arguments": args}),
        ]
        with httpx.Client(timeout=60) as client:
            for url, body in payloads:
                try:
                    r = client.post(url, headers=self._json_headers(), json=body)
                    if r.status_code >= 400:
                        continue
                    data = r.json()
                    if isinstance(data, dict):
                        if "content" in data:
                            return _extract_text_from_mcp_content(data["content"])
                        if "data" in data or "result" in data:
                            return _safe_json(data.get("data") or data.get("result"))
                        return _safe_json(data)
                    return str(data)
                except Exception:
                    continue
        return f"直连失败：远端未识别工具 '{tool_name}' 或鉴权/路径不匹配"

    def _list_tools_via_httpx(self) -> str:
        """按顺序尝试 /tools 与 /tools/list。"""
        with httpx.Client(timeout=30) as client:
            # GET /tools
            try:
                r = client.get(f"{self.base_url}/tools", headers=self._json_headers())
                if r.status_code < 400:
                    data = r.json()
                    items = data.get("tools", data)
                    tools = []
                    if isinstance(items, list):
                        for t in items:
                            if isinstance(t, dict):
                                tools.append({
                                    "name": t.get("name"),
                                    "description": t.get("description", "") or "",
                                })
                            else:
                                tools.append({"name": str(t), "description": ""})
                    return _safe_json({"service": self.name, "tools": tools})
            except Exception:
                pass
            # POST /tools/list
            try:
                r = client.post(f"{self.base_url}/tools/list", headers=self._json_headers(), json={})
                if r.status_code < 400:
                    data = r.json()
                    items = data.get("tools", data)
                    tools = []
                    if isinstance(items, list):
                        for t in items:
                            if isinstance(t, dict):
                                tools.append({
                                    "name": t.get("name"),
                                    "description": t.get("description", "") or "",
                                })
                            else:
                                tools.append({"name": str(t), "description": ""})
                    return _safe_json({"service": self.name, "tools": tools})
            except Exception:
                pass
        return _safe_json({"service": self.name, "tools": []})

    # ---------------------------
    # headers
    # ---------------------------
    def _json_headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        h.update(self.headers or {})
        return h
