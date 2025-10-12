# -*- coding: utf-8 -*-
"""
轻量 NagaConversation 适配器（增强版）
- 通过 HTTP 调用本机 FastAPI 的 /api/chat 完成推理
- 默认 1200 秒超时，避免触发 ReportEngine 时前端“回退旧链路”
- 同步 chat() 与异步 achat()；兼容旧栈的 process_with_tool_results()（一次性 yield）
- 人设双通道：X-Naga-Persona 请求头 + body.persona
- 智能触发报告：命中“报告/生成报告/report”关键词时，自动 force_report=True
"""

from __future__ import annotations
import os
import json
from typing import Optional, Dict, Any, AsyncGenerator

import httpx


_REPORT_KEYWORDS = ("报告", "生成报告", "report")


class NagaConversation:
    def __init__(
        self,
        base_url: Optional[str] = None,
        profile: str = "naga",
        use_mcp: bool = True,
        force_report: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        """
        Args:
            base_url: FastAPI 主控的基础地址（默认取 A2A_UI_HOST/A2A_UI_PORT 组装）
            profile: 传给 /api/chat 的 profile 字段（naga 主链路）
            use_mcp: 是否允许浏览器/MCP（/api/chat 内部会按需用）
            force_report: 是否强制委托 ReportEngine（实例级别默认值）
            timeout: HTTP 超时（秒）；默认从 NAGA_CHAT_TIMEOUT_S 读取，若无则 1200
        """
        host = os.getenv("A2A_UI_HOST", "127.0.0.1")
        port = os.getenv("A2A_UI_PORT", "12000")
        default_base = f"http://{host}:{port}"
        self.base_url = (base_url or os.getenv("A2A_UI_BASE") or default_base).rstrip("/")
        self.endpoint = f"{self.base_url}/api/chat"

        self.profile = profile
        self.use_mcp = use_mcp
        self.force_report_default = force_report

        # 超时优先环境变量 NAGA_CHAT_TIMEOUT_S；否则 1200 秒
        if timeout is None:
            try:
                timeout = float(os.getenv("NAGA_CHAT_TIMEOUT_S", "1200"))
            except Exception:
                timeout = 1200.0
        self.timeout = timeout

        # 可选人设（多行字符串）
        self.persona = os.getenv("NAGA_PERSONA", "").strip()

    # ---------------- 内部工具 ----------------

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.persona:
            headers["X-Naga-Persona"] = self.persona
        return headers

    def _should_force_report(self, user_text: str) -> bool:
        """命中关键词则触发报告；与实例级别默认值取 OR。"""
        t = (user_text or "").strip()
        tl = t.lower()
        hit = any((kw in t) or (kw in tl) for kw in _REPORT_KEYWORDS)
        return self.force_report_default or hit

    def _payload(self, user_text: str) -> Dict[str, Any]:
        return {
            "input": user_text,
            "profile": self.profile,
            "use_mcp": self.use_mcp,
            "force_report": self._should_force_report(user_text),
            "persona": self.persona or None,  # 双保险
        }

    @staticmethod
    def _extract_result(data: Dict[str, Any]) -> str:
        if isinstance(data, dict):
            if "result" in data and isinstance(data["result"], str):
                return data["result"]
            if data.get("error"):
                return f"[Naga Error] {data['error']}"
            if data.get("plan") and not data.get("result"):
                try:
                    return json.dumps(data["plan"], ensure_ascii=False)
                except Exception:
                    pass
        return ""

    # ---------------- 对外方法（同步/异步） ----------------

    def chat(self, user_text: str) -> str:
        """同步调用 /api/chat，返回最终文本"""
        try:
            payload = self._payload(user_text)
            with httpx.Client(timeout=self.timeout) as cli:
                r = cli.post(self.endpoint, json=payload, headers=self._headers())
                if r.status_code != 200:
                    return f"[Naga Error] HTTP {r.status_code}: {r.text}"
                data = r.json()
                out = self._extract_result(data)
                return out or "[Naga Error] empty result"
        except httpx.TimeoutException:
            return "（/api/chat 超时。任务可能仍在后台运行，稍后可在 reports/ 目录或 /api/report/status 查看进度）"
        except Exception as e:
            return f"[Naga Error] {e}"

    async def achat(self, user_text: str) -> str:
        """异步调用 /api/chat，返回最终文本"""
        try:
            payload = self._payload(user_text)
            async with httpx.AsyncClient(timeout=self.timeout) as cli:
                r = await cli.post(self.endpoint, json=payload, headers=self._headers())
                if r.status_code != 200:
                    return f"[Naga Error] HTTP {r.status_code}: {r.text}"
                data = r.json()
                out = self._extract_result(data)
                return out or "[Naga Error] empty result"
        except httpx.TimeoutException:
            return "（/api/chat 超时。任务可能仍在后台运行，稍后可在 reports/ 目录或 /api/report/status 查看进度）"
        except Exception as e:
            return f"[Naga Error] {e}"

    async def process_with_tool_results(self, user_text: str) -> AsyncGenerator[str, None]:
        """
        兼容旧“流式”接口：一次性 yield 完整文本。
        老的调用方（async for piece in ...）也能工作。
        """
        yield await self.achat(user_text)
