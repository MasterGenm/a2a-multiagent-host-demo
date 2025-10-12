# -*- coding: utf-8 -*-
"""
mcp_manager.py — 可直接替换版（仅小补丁：服务查找走 get_service）
- 保留 demo.echo
- 新增 siliconflow.images.generate (文生图，直打 /images/generations)
- 兼容你原有的 AgentScope HttpStatelessClient 远程 MCP 走法
"""

from __future__ import annotations
from typing import Any, Dict, List
import os
import json
import asyncio
import httpx

# —— 可选：AgentScope 远程 MCP 客户端（你的原始思路保留）——
try:
    from agentscope.mcp import HttpStatelessClient
    AGENTSCOPE_AVAILABLE = True
except Exception:
    AGENTSCOPE_AVAILABLE = False

# —— 尝试从你的配置系统拿 API Key/Base URL（可被环境变量覆盖）——
try:
    # 你工程里通常在 mcpserver/config.py，有 load_config()
    from mcpserver.config import load_config  # 优先这个
except Exception:
    try:
        from config import load_config         # 备选
    except Exception:
        load_config = None

# =========================================================
# 1) 注册表相关（沿用你原有的合并逻辑：外部 + 本地 demo）
# =========================================================
_MCP_REGISTRY: Dict[str, Any] = {}
_get_all_services_info = None
_refresh_registry = None

# 注意：这里的 _EXT_REGISTRY 用来“引用”外部注册表对象本身（可被刷新）
try:
    from mcpserver.mcp_registry import MCP_REGISTRY as _EXT_REGISTRY  # type: ignore
except Exception:
    try:
        from mcp_registry import MCP_REGISTRY as _EXT_REGISTRY  # 兼容无包前缀
    except Exception:
        _EXT_REGISTRY = {}

# 引用可选的工具函数
try:
    from mcpserver.mcp_registry import get_all_services_info as _get_all_services_info  # type: ignore
except Exception:
    try:
        from mcp_registry import get_all_services_info as _get_all_services_info
    except Exception:
        _get_all_services_info = None

try:
    from mcpserver.mcp_registry import refresh_registry as _refresh_registry  # type: ignore
except Exception:
    try:
        from mcp_registry import refresh_registry as _refresh_registry
    except Exception:
        _refresh_registry = None

# ★ PATCH: 新增安全导入 get_service（用于别名/宽松匹配）
_get_service = None
try:
    from mcpserver.mcp_registry import get_service as _get_service  # type: ignore
except Exception:
    try:
        from mcp_registry import get_service as _get_service
    except Exception:
        _get_service = None

def _env_truthy(key: str, default: str = "true") -> bool:
    val = os.getenv(key, default).strip().lower()
    return val in ("1", "true", "yes", "on", "y")

_ENABLE_DEMO = _env_truthy("NAGA_MCP_ENABLE_DEMO", "true")
_LOCAL_DEMO_REGISTRY: Dict[str, Any] = {}

# =========================================================
# 2) 内置 demo.echo
# =========================================================
class _EchoDemoService:
    """最小可用 Demo：demo.echo"""
    display_name = "Echo Demo (内置)"
    description = "用于联调与自测（echo/ping）"
    available_tools = [
        {
            "name": "ping",
            "description": "连通性测试，返回 pong",
            "example": '{"agentType":"mcp","service_name":"demo.echo","tool_name":"ping"}'
        },
        {
            "name": "echo",
            "description": "回声输出（text 或 input）",
            "example": '{"agentType":"mcp","service_name":"demo.echo","tool_name":"echo","text":"hello"}'
        }
    ]

    async def call(self, tool_name: str, **kwargs):
        if tool_name == "ping":
            return "success: pong"
        if tool_name == "echo":
            payload = kwargs.get("text") or kwargs.get("input") or kwargs
            if not isinstance(payload, str):
                try:
                    payload = json.dumps(payload, ensure_ascii=False)
                except Exception:
                    payload = str(payload)
            return f"success: {payload}"
        return {"status": "error", "error": f"unknown tool: {tool_name}"}

if _ENABLE_DEMO:
    _LOCAL_DEMO_REGISTRY["demo.echo"] = _EchoDemoService()

# =========================================================
# 3) 硅智 - 文生图直打（非 AgentScope）
# =========================================================
async def _siliconflow_images_generate(prompt: str, **kwargs) -> str:
    """直接调用 SiliconFlow /images/generations"""
    try:
        api_key = os.environ.get("SILICONFLOW_API_KEY") or (load_config() and getattr(load_config(), "siliconflow_api_key", None))
        if not api_key:
            return "调用失败：缺少 SILICONFLOW_API_KEY"

        url = "https://api.siliconflow.cn/v1/images/generations"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        body = {
            "prompt": prompt,
            "model": kwargs.get("model", "black-forest-labs/FLUX.1-schnell"),
            "n": kwargs.get("n", 1),
            "size": kwargs.get("size", "1024x1024"),
            "response_format": "url"
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=body, timeout=60)
            resp.raise_for_status()
            data = resp.json()

        items = data.get("data", [])
        lines = []
        for i, item in enumerate(items, 1):
            if item.get("url"):
                lines.append(f"{i}. {item['url']}")
            elif item.get("b64_json"):
                lines.append(f"{i}. [base64 图片数据，长度 {len(item['b64_json'])}]")
            else:
                lines.append(f"{i}. [未知返回项]")
        return "\n".join(lines)
    except Exception as e:
        return f"调用失败：{e}"

# =========================================================
# 4) 合并注册表（外部 + 本地）
# =========================================================
def _merged_registry() -> Dict[str, Any]:
    merged = dict(_LOCAL_DEMO_REGISTRY)
    # 注意：_EXT_REGISTRY 引用外部注册表对象本身；若未加载，尝试刷新一次
    if _EXT_REGISTRY and isinstance(_EXT_REGISTRY, dict) and _EXT_REGISTRY:
        for k, v in _EXT_REGISTRY.items():
            merged[k] = v
    elif callable(_refresh_registry):
        try:
            _refresh_registry()
            if _EXT_REGISTRY:
                for k, v in _EXT_REGISTRY.items():
                    merged[k] = v
        except Exception:
            pass
    return merged

# =========================================================
# 5) 统一调用入口（工具回路会 await 这里）
# =========================================================
# === 替换 mcp_manager.py 里的 unified_call 整个函数 ===
async def unified_call(service_name: str, tool_name: str, **kwargs) -> str:
    """
    统一调用入口：
    - 支持特俗内省工具：__list_tools / __tools / __help（不会转发到远程，直接本地列出）
    - 如果拿不到 tools 清单，也不会提前拦截，先“乐观直调”远程；远程再报 Unknown tool 时再返回
    """
    import inspect
    import json as _json

    # 1) 取服务（带别名&大小写兼容）
    svc = None
    if callable(_get_service):
        svc = _get_service(service_name)
    else:
        # 兜底：直接用注册表（不推荐，但保证不崩）
        try:
            from mcpserver.mcp_registry import MCP_REGISTRY as _REG
        except Exception:
            from mcp_registry import MCP_REGISTRY as _REG
        svc = _REG.get(service_name)

    if not svc:
        return f"调用失败：未找到服务 '{service_name}'"

    # 2) 内省：__list_tools / __tools / __help
    if tool_name in ("__list_tools", "__tools", "__help", "__ls"):
        try:
            tools = None
            # 优先远程接口
            if hasattr(svc, "list_tools") and callable(getattr(svc, "list_tools")):
                maybe = svc.list_tools()
                tools = await maybe if inspect.iscoroutine(maybe) else maybe
            # 退回到对象上的 available_tools（本地 agent 通常会带）
            if not tools:
                tools = getattr(svc, "available_tools", [])

            # 归一化
            norm = []
            for t in tools or []:
                if isinstance(t, dict):
                    name = t.get("name") or t.get("command") or t.get("tool") or t.get("id")
                    desc = t.get("description", "")
                    example = t.get("example") or t.get("examples")
                    norm.append({"name": name, "description": desc, "example": example})
                elif isinstance(t, str):
                    norm.append({"name": t, "description": ""})

            return _json.dumps(
                {"service": service_name, "tools": norm},
                ensure_ascii=False, indent=2
            )
        except Exception as e:
            return f"列工具失败：{e}"

    # 3) 正常调用路径（拿不到工具清单也不拦，先直调）
    # 清洗一下参数，去掉 None
    args = {k: v for k, v in kwargs.items() if v is not None}

    try:
        # 优先远程代理/统一接口
        if hasattr(svc, "invoke") and callable(getattr(svc, "invoke")):
            res = svc.invoke(tool_name, **args)
            res = await res if inspect.iscoroutine(res) else res

        # 兼容一些本地 Agent 的自定义总入口
        elif hasattr(svc, "call_tool") and callable(getattr(svc, "call_tool")):
            res = svc.call_tool(tool_name, **args)
            res = await res if inspect.iscoroutine(res) else res

        # 兼容一些本地 Agent 的 call 方法
        elif hasattr(svc, "call") and callable(getattr(svc, "call")):
            res = svc.call(tool_name, **args)
            res = await res if inspect.iscoroutine(res) else res

        # 兼容"同名方法就是工具"的写法
        elif hasattr(svc, tool_name) and callable(getattr(svc, tool_name)):
            fn = getattr(svc, tool_name)
            res = await fn(**args) if inspect.iscoroutinefunction(fn) else fn(**args)

        else:
            # 这一步再去拿一次工具清单，给出友好提示
            avail = []
            try:
                if hasattr(svc, "list_tools") and callable(getattr(svc, "list_tools")):
                    maybe = svc.list_tools()
                    avail = await maybe if inspect.iscoroutine(maybe) else maybe
                if not avail:
                    avail = getattr(svc, "available_tools", [])
            except Exception:
                pass

            names = []
            for t in avail or []:
                if isinstance(t, dict) and "name" in t:
                    names.append(t["name"])
                elif isinstance(t, str):
                    names.append(t)
            hint = ", ".join(names) if names else "未知（先发 __list_tools 获取）"
            return f"调用失败：服务 '{service_name}' 不支持工具 '{tool_name}'；可用工具：{hint}"

        # 统一格式化返回
        if isinstance(res, (dict, list)):
            return _json.dumps(res, ensure_ascii=False)
        return str(res)

    except Exception as e:
        # 将远程"Unknown tool ..."等真实报错透传出来，便于定位
        return f"调用失败：{e}"



# =========================================================
# 6) 服务清单（给 UI / 提示词）
# =========================================================
def _safe_services_info_from_registry() -> Dict[str, Dict[str, Any]]:
    if callable(_get_all_services_info):
        try:
            info = _get_all_services_info()
            if isinstance(info, dict) and info:
                return info
        except Exception:
            pass

    info: Dict[str, Dict[str, Any]] = {}
    for name, svc in _merged_registry().items():
        tools: List[Dict[str, Any]] = []
        meta_tools = getattr(svc, "available_tools", None) or getattr(svc, "tools", None)
        if isinstance(meta_tools, list) and meta_tools:
            for t in meta_tools:
                tname = (t.get("name") if isinstance(t, dict) else None) or ""
                if not tname:
                    continue
                tdesc = (t.get("description") if isinstance(t, dict) else "") or ""
                texample = (t.get("example") if isinstance(t, dict) else "") or ""
                tools.append({"name": tname, "description": tdesc, "example": texample})
        else:
            for attr in dir(svc):
                if attr.startswith("_") or attr in ("call", "close", "shutdown"):
                    continue
                val = getattr(svc, attr)
                if callable(val):
                    tools.append({"name": attr, "description": "", "example": ""})

        info[name] = {
            "display_name": getattr(svc, "display_name", name),
            "description": getattr(svc, "description", ""),
            "available_tools": tools,
        }
    return info

def get_available_services_filtered() -> Dict[str, Any]:
    services_info = _safe_services_info_from_registry()
    mcp_list: List[Dict[str, Any]] = []
    for name, meta in services_info.items():
        display = meta.get("display_name") or meta.get("label") or meta.get("displayName") or name
        desc = meta.get("description") or meta.get("desc") or ""
        tools = meta.get("available_tools") or meta.get("tools") or []
        mcp_list.append({
            "name": name, "label": display, "desc": desc,
            "available_tools": tools, "tools": tools
        })
    return {"mcp_services": mcp_list, "agent_services": []}

# =========================================================
# 7) 远程 MCP 服务 URL（仅用于 AgentScope 客户端）
# =========================================================
def _get_mcp_service_url(service_name: str) -> str:
    defaults = {
        "bailian": "http://localhost:8000",
        "siliconflow": "http://localhost:8888",  # 这里指的是"你的 MCP 代理"，不是官方 API
        "naga": os.getenv("NAGA_MCP_URL", "http://localhost:8001"),
    }
    env_url = os.getenv(f"{service_name.upper()}_MCP_URL")
    return env_url or defaults.get(service_name.lower(), "http://localhost:8000")

# ===== 在 mcp_manager.py 末尾追加：MCP 管理门面 =====

class _MCPManagerFacade:
    """兼容你旧版的 .xxx 属性访问"""
    @staticmethod
    def unified_call(*args, **kwargs):
        # 注意：这是同步包装异步函数
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        coro = unified_call(*args, **kwargs)
        if loop and loop.is_running():
            # 有正在运行的 loop：开任务等结果
            return asyncio.run_coroutine_threadsafe(coro, loop).result()
        else:
            return asyncio.run(coro)

    @staticmethod
    def get_available_services_filtered():
        return get_available_services_filtered()

# 兼容旧引用
MCPManager = _MCPManagerFacade