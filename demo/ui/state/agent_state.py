import mesop as me
from typing import Dict, List, Optional, Any
from dataclasses import field


@me.stateclass
class AgentState:
    """Agents List State"""

    agent_dialog_open: bool = False
    agent_address: str = ''
    agent_name: str = ''
    agent_description: str = ''
    input_modes: list[str]
    output_modes: list[str]
    stream_supported: bool = False
    push_notifications_supported: bool = False
    error: str = ''
    agent_framework_type: str = ''


@me.stateclass
class PersonaState:
    """Client Agent 人设状态管理"""
    
    # 人设选择
    active_persona_id: str = "assistant"
    available_personas: List[Dict[str, Any]] = field(default_factory=list)
    persona_selector_open: bool = False
    
    # 人设配置
    current_persona_config: Optional[Dict[str, Any]] = None
    persona_greeting_shown: bool = False
    
    # 人设管理界面
    persona_management_open: bool = False
    editing_persona_id: Optional[str] = None
    new_persona_dialog_open: bool = False


@me.stateclass 
class RemoteAgentState:
    """Remote Agent 调度状态管理"""
    
    # 代理状态
    available_agents: List[Dict[str, Any]] = field(default_factory=list)
    agent_status_map: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # 任务调度
    active_tasks: List[Dict[str, Any]] = field(default_factory=list)
    task_history: List[Dict[str, Any]] = field(default_factory=list)
    
    # 调度统计
    total_dispatched_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    
    # UI状态
    agent_monitor_open: bool = False
    task_queue_view_open: bool = False
    dispatch_in_progress: bool = False
    last_dispatch_result: Optional[str] = None


@me.stateclass
class EnhancedConversationState:
    """增强的对话状态，集成人设和代理调度"""
    
    # 对话基础信息
    conversation_id: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    
    # 人设相关
    current_persona_context: Optional[Dict[str, Any]] = None
    persona_system_prompt: str = ""
    
    # 代理调度相关
    auto_dispatch_enabled: bool = True
    dispatch_threshold: float = 0.6
    pending_remote_tasks: List[str] = field(default_factory=list)
    
    # 模型配置
    current_model_provider: str = "zhipu"  # zhipu/ollama
    current_model_name: str = "glm-4.5-flash"
    model_temperature: float = 0.7
    model_max_tokens: int = 2048
    
    # 对话控制
    streaming_enabled: bool = True
    auto_save_enabled: bool = True
    context_window_size: int = 10
