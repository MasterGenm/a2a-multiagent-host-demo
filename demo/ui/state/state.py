# state/state.py (最终健壮版，可直接替换)

import dataclasses
import datetime
import uuid
from typing import Any, Literal, List, Dict

import mesop as me
from dataclasses import dataclass, field

# =================================================================
#  核心数据结构 (新标准)
# =================================================================

@dataclass
class ChatMessage:
    """
    定义一条聊天消息的数据结构。
    关键修复：为 role 和 content 提供默认值，以防止在任何情况下
    因无参调用 __init__ 而导致 TypeError，特别是在二次交互时。
    """
    role: Literal["user", "model"] = "model"  # <-- 关键修复：添加默认值
    content: str = ""                         # <-- 关键修复：添加默认值
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    media_type: str = "text"                  # 添加媒体类型支持

@dataclass
class AgentTask:
    """定义一个后台代理任务的数据结构。"""
    task_id: str
    prompt: str
    status: Literal["Pending", "Running", "Success", "Failed"] = "Pending"
    result: str = ""
    start_time: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

# =================================================================
#  为兼容 host_agent_service.py 而保留的旧数据结构
# =================================================================

ContentPart = str | dict[str, Any]

@dataclass
class StateConversation:
    conversation_id: str = ''
    conversation_name: str = ''
    is_active: bool = True
    message_ids: list[str] = field(default_factory=list)

@dataclass
class StateMessage:
    message_id: str = ''
    task_id: str = ''
    context_id: str = ''
    role: str = ''
    content: list[tuple[ContentPart, str]] = field(default_factory=list)

@dataclass
class StateTask:
    """恢复 StateTask 以兼容 host_agent_service 中的 convert_task_to_state"""
    task_id: str = ''
    context_id: str | None = None
    state: str | None = None
    message: StateMessage = field(default_factory=StateMessage)
    artifacts: list[list[tuple[ContentPart, str]]] = field(default_factory=list)

@dataclass
class SessionTask:
    """恢复 SessionTask 以解决 ImportError"""
    context_id: str = ''
    task: StateTask = field(default_factory=StateTask)

@dataclass
class StateEvent:
    context_id: str = ''
    actor: str = ''
    role: str = ''
    id: str = ''
    content: list[tuple[ContentPart, str]] = field(default_factory=list)

# =================================================================
#  全局应用状态 (最终合并版)
# =================================================================

@me.stateclass
class AppState:
    is_initialized: bool = False
    ollama_connected: bool = False
    available_models: List[str] = field(default_factory=list)

    # 建议：默认就用一个一定存在的占位值
    selected_model: str = "naga:default"

    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    user_input: str = ""
    messages: List[ChatMessage] = field(default_factory=list)
    tasks: Dict[str, AgentTask] = field(default_factory=dict)

    # ✅ 新增：回复语言，和 main.py 对齐
    #    （与 main.py 的 LANGUAGE_ALIASES / LANGUAGE_DIRECTIVES 一致）
    reply_lang: Literal["auto", "zh", "en", "ja", "ko"] = "auto"

    # 人设与调度
    active_persona_id: str = "assistant"
    persona_selector_open: bool = False
    remote_agent_monitor_open: bool = False
    auto_dispatch_enabled: bool = True
    current_model_provider: str = "zhipu"

    # 调试面板与最近一次调用的状态
    debug_panel_open: bool = False
    debug_mode: bool = False  # 控制是否在对话中插入 Plan/Language/Memory 调试消息
    used_query_engine: bool = False
    used_report_engine: bool = False
    used_grag_memory: bool = False
    last_report_path: str = ""
    last_task: str = ""
    last_memory_snippet: str = ""
    debug_show_plan: bool = True
    debug_show_memory: bool = True
    debug_show_language: bool = True

    # a2a 兼容字段
    sidenav_open: bool = False
    theme_mode: Literal['system', 'light', 'dark'] = 'system'
    current_conversation_id: str = ''
    conversations: list[StateConversation] = field(default_factory=list)

    task_list: list[SessionTask] = field(default_factory=list)
    legacy_messages: list[StateMessage] = field(default_factory=list)
    background_tasks: dict[str, str] = field(default_factory=dict)
    completed_forms: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    form_responses: dict[str, str] = field(default_factory=dict)
    api_key: str = ''
    uses_vertex_ai: bool = False
    api_key_dialog_open: bool = False

# --- 辅助函数 ---
def is_form(message: StateMessage) -> bool:
    """检查一条旧版消息是否是表单请求。"""
    if any(part[1] == 'form' for part in message.content):
        return True
    return False

def form_sent(message: StateMessage, app_state: AppState) -> bool:
    """检查一个表单是否已经被提交。"""
    return message.message_id in app_state.form_responses
