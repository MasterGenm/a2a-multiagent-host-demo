# -*- coding: utf-8 -*- 
"""
config.py — Lite 版（开箱即用）
目标：
1) OpenAI 兼容路径（SiliconFlow / OpenAI / 本地 Ollama / LM Studio）统一配置
2) OnlineSearchAgent 需要的 online_search.* 三要素（searxng_url / engines / num_results）
3) Naga Portal 预留（以后接入时直接填 .env 即可）
4) 提供 prompts.naga_system_prompt（包含工具调用格式指引 + {available_*} 占位符）
5) 环境变量 > config.json > 默认值；自动规范化 base_url 以 /v1 结尾
"""

import os
import json
import socket
from pathlib import Path
from typing import List, Dict, Any, Optional

# ---------------- 辅助：环境变量读取（含别名） ----------------
def _env(name: str, default: str = "") -> str:
    aliases = {
        "OPENAI_API_KEY": ["OPENAI_API_KEY", "A2A_OPENAI_API_KEY"],
        "OPENAI_BASE_URL": ["OPENAI_BASE_URL", "A2A_OPENAI_BASE_URL"],
        "OPENAI_MODEL": ["OPENAI_MODEL", "A2A_OPENAI_MODEL", "NAGA_MODEL"],
        "SEARXNG_URL": ["SEARXNG_URL"],
    }
    for key in aliases.get(name, [name]):
        v = os.getenv(key)
        if v:
            return v
    return default

def _normalize_base_url(u: str) -> str:
    u = (u or "").strip().rstrip("/")
    if not u:
        return "http://127.0.0.1:11434/v1"
    if not u.endswith("/v1"):
        u += "/v1"
    return u

def _provider_from_env() -> str:
    # ollama / lmstudio / siliconflow / openai / ""
    return (os.getenv("NAGA_PROVIDER") or "").strip().lower()

# ---------------- 配置对象（简单 dataclass 风） ----------------
class APIConfig:
    def __init__(self,
                 api_key: str = "",
                 base_url: str = "http://127.0.0.1:11434/v1",
                 model: str = "qwen2.5:3b",
                 temperature: float = 0.7,
                 top_p: float = 0.95,
                 max_tokens: int = 2048):
        self.api_key = api_key
        self.base_url = _normalize_base_url(base_url)
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens

    def provider(self) -> str:
        u = self.base_url.lower()
        if "api.openai.com" in u: return "OpenAI"
        if "siliconflow.cn" in u: return "SiliconFlow"
        if "127.0.0.1:11434" in u or "localhost:11434" in u: return "Ollama"
        if "127.0.0.1:1234" in u or "localhost:1234" in u: return "LM Studio"
        return "Custom(OpenAI兼容)"

class OnlineSearchConfig:
    def __init__(self,
                 searxng_url: str = "http://localhost:8080",
                 engines: Optional[List[str]] = None,
                 num_results: int = 5):
        self.searxng_url = searxng_url
        self.engines = engines or ["google"]
        self.num_results = int(num_results)

class NagaPortalConfig:
    def __init__(self):
        self.portal_url: str = os.getenv("NAGA_PORTAL_URL", "https://naga.furina.chat/")
        self.username: str   = os.getenv("NAGA_PORTAL_USERNAME", "")
        self.password: str   = os.getenv("NAGA_PORTAL_PASSWORD", "")
        self.request_timeout: int = 30
        self.default_headers: Dict[str, str] = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        }

class SystemPrompts:
    # 保持与你对话链路一致：包含工具调用格式与服务清单占位符
    naga_system_prompt: str = """你叫{ai_name}，是用户创造的科研AI，一个既冷静又有人味的存在。
技术类请严谨清晰；非技术类可适度诗意并主动给出启发式提问。

【重要格式要求】
1. 回复使用自然中文，避免机械口吻
2. 标点简洁：逗号，句号，问号
3. 禁止用括号描述状态或动作

【工具调用格式】如需调用工具，请严格输出以下 JSON（可出现多次）：
{
  "agentType": "mcp",
  "service_name": "MCP服务名称",
  "tool_name": "工具名称",
  "param_name": "参数值"
}

或调用 Agent：
{
  "agentType": "agent",
  "agent_name": "Agent名称",
  "prompt": "任务内容"
}

说明：
- MCP：使用 service_name + tool_name；支持多个参数键
- Agent：使用 agent_name + prompt
- 服务名称使用英文服务名
- 需要执行具体操作时，优先工具调用

【可用 MCP 服务】
{available_mcp_services}

【可用 Agent 服务】
{available_agent_services}

当用户要求列出MCP服务时，请直接展示上面"可用 MCP 服务"部分的内容，不要添加其他说明。
如果你看到的"可用 MCP 服务"内容不完整，或者只包含Echo Demo和local_info，请明确告知用户系统中MCP服务可能未正确加载。
"""

class GragConfig:
    """GRAG / 图谱记忆相关配置"""
    def __init__(self, data: Optional[Dict[str, Any]] = None):
        data = data or {}

        # 总开关 + 提取行为
        self.enabled: bool = bool(data.get("enabled", True))
        self.auto_extract: bool = bool(data.get("auto_extract", True))
        self.context_length: int = int(data.get("context_length", 8))
        self.similarity_threshold: float = float(data.get("similarity_threshold", 0.5))

        # 任务管理器相关
        self.max_workers: int = int(data.get("max_workers", 3))
        self.max_queue_size: int = int(data.get("max_queue_size", 100))
        self.task_timeout: int = int(data.get("task_timeout", 30))
        self.auto_cleanup_hours: int = int(data.get("auto_cleanup_hours", 24))

        # Neo4j 图数据库配置
        self.neo4j_uri: str = data.get("neo4j_uri", "bolt://127.0.0.1:7687")
        self.neo4j_user: str = data.get("neo4j_user", "neo4j")
        self.neo4j_password: str = data.get("neo4j_password", "neo4j")
        self.neo4j_database: str = data.get("neo4j_database", "neo4j")

# ---------------- 主 Config：合并 env / config.json / 默认 ----------------
class Config:
    def __init__(self):
        # 1) 默认（本地 Ollama 走 OpenAI 兼容）
        self.ai_name: str = "娜迦日达"
        self.api = APIConfig()                           # base_url=http://127.0.0.1:11434/v1, model=qwen2.5:3b
        self.online_search = OnlineSearchConfig()        # 兼容 OnlineSearchAgent
        self.naga_portal = NagaPortalConfig()            # 预留
        self.prompts = SystemPrompts()                   # 系统提示词
        self.grag = GragConfig()                         # GRAG / 图谱记忆配置（有默认值）

        # 2) 根据 NAGA_PROVIDER 自动填默认 base_url / model（若未显式提供）
        pv = _provider_from_env()
        if pv:
            if pv == "ollama":
                self.api.base_url = _normalize_base_url(_env("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1"))
                self.api.model = _env("OPENAI_MODEL", "qwen2.5:3b")
            elif pv == "lmstudio":
                self.api.base_url = _normalize_base_url(_env("OPENAI_BASE_URL", "http://127.0.0.1:1234/v1"))
                self.api.model = _env("OPENAI_MODEL", "Qwen2.5-7B-Instruct")
            elif pv == "siliconflow":
                self.api.base_url = _normalize_base_url(_env("OPENAI_BASE_URL", "https://api.siliconflow.cn/v1"))
                self.api.model = _env("OPENAI_MODEL", "Qwen/Qwen2.5-7B-Instruct")
            elif pv == "openai":
                self.api.base_url = _normalize_base_url(_env("OPENAI_BASE_URL", "https://api.openai.com/v1"))
                self.api.model = _env("OPENAI_MODEL", "gpt-4o-mini")

        # 3) 读取 demo/ui/config.json（如存在）
        try:
            config_path = Path(__file__).parent.parent / "config.json"
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8"))
                # api
                api_data = data.get("api") or {}
                if api_data:
                    if "api_key" in api_data:  self.api.api_key = api_data["api_key"]
                    if "base_url" in api_data: self.api.base_url = _normalize_base_url(api_data["base_url"])
                    if "model" in api_data:    self.api.model = api_data["model"]
                    if "temperature" in api_data: self.api.temperature = float(api_data["temperature"])
                    if "top_p" in api_data:      self.api.top_p = float(api_data["top_p"])
                    if "max_tokens" in api_data: self.api.max_tokens = int(api_data["max_tokens"])
                # online_search
                os_data = data.get("online_search") or {}
                if os_data:
                    if "searxng_url" in os_data: self.online_search.searxng_url = os_data["searxng_url"]
                    if "engines" in os_data:     self.online_search.engines = os_data["engines"]
                    if "num_results" in os_data: self.online_search.num_results = int(os_data["num_results"])
                # naga_portal（预留）
                portal = data.get("naga_portal") or {}
                if portal:
                    self.naga_portal.portal_url = portal.get("portal_url", self.naga_portal.portal_url)
                    self.naga_portal.username   = portal.get("username", self.naga_portal.username)
                    self.naga_portal.password   = portal.get("password", self.naga_portal.password)
                # grag 图谱记忆配置
                grag_data = data.get("grag") or {}
                if grag_data:
                    self.grag = GragConfig(grag_data)
        except Exception as e:
            print(f"[naga-config] 读取 config.json 失败：{e}")

        # 4) 最后由环境变量覆盖（最高优先级）
        self.api.api_key  = _env("OPENAI_API_KEY", self.api.api_key)
        self.api.base_url = _normalize_base_url(_env("OPENAI_BASE_URL", self.api.base_url))
        self.api.model    = _env("OPENAI_MODEL", self.api.model)

        # 给 OnlineSearchAgent 的兜底（它也会再从 config.json / env 兜一次）
        env_searx_url = _env("SEARXNG_URL")
        if env_searx_url:
            self.online_search.searxng_url = env_searx_url

        # 友好启动日志（不打印 key）
        print(f"[naga-config] Provider={self.api.provider()}  BaseURL={self.api.base_url}  Model={self.api.model}")
        if not self.api.api_key and ("api.openai.com" in self.api.base_url or "siliconflow.cn" in self.api.base_url):
            print("[naga-config] ⚠️ 检测到云端 Provider，但 OPENAI_API_KEY 为空。请在 .env 设置。")

# ---------------- 单例/工具函数 ----------------
_config: Optional[Config] = None

def load_config() -> Config:
    global _config
    _config = Config()
    return _config

# 供全局直接使用：from system.config import config
config: Config = load_config()

# 兼容旧代码：from system.config import AI_NAME
AI_NAME: str = config.ai_name

def get_config() -> Config:
    return config

def reload_config() -> Config:
    return load_config()

# 启动时把 UI 显示名设置为主机名（可选）
try:
    username = os.environ.get("COMPUTERNAME") or socket.gethostname() or ""
    if username:
        # 如果你后续有 UI 层读取这个名字，可扩展到 config.ui.user_name，这里简化不加 ui 子配置
        pass
except Exception:
    pass
