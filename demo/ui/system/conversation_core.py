# 标准库导入
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, AsyncGenerator
import httpx

from naga_core.system.config import get_config

logger = logging.getLogger(__name__)

class ConversationCore:
    def __init__(self):
        self.config = get_config()
        self.base_url = self.config.api.base_url
        self.api_key = self.config.api.api_key
        self.model = self.config.api.model
        
    async def chat_with_tools(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> AsyncGenerator[str, None]:
        """
        与模型对话并处理工具调用
        """
        # 构建请求头
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            
        # 构建请求体
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True
        }
        
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
            
        full_response = ""
        
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST", 
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30.0
                ) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]  # 移除 "data: " 前缀
                            if data_str.strip() == "[DONE]":
                                break
                                
                            try:
                                data = json.loads(data_str)
                                if "choices" in data and len(data["choices"]) > 0:
                                    delta = data["choices"][0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        full_response += content
                                        yield content
                            except json.JSONDecodeError:
                                continue
                                
        except Exception as e:
            logger.error(f"Error in chat streaming: {e}")
            yield f"Error: {str(e)}"
            
        # 检查是否有工具调用需要处理
        if full_response:
            from mcpserver.tool_call_utils import parse_tool_calls, execute_tool_calls
            tool_calls = parse_tool_calls(full_response)
            if tool_calls:
                tool_result = execute_tool_calls(tool_calls)
                yield f"\n\n工具调用结果：\n{tool_result}"

# 第三方库导入
from openai import AsyncOpenAI

# 本地模块导入
from apiserver.tool_call_utils import tool_call_loop
from system.config import config, AI_NAME
from mcpserver.mcp_manager import get_mcp_manager
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX
# from thinking import TreeThinkingEngine
# from thinking.config import COMPLEX_KEYWORDS  # 已废弃，不再使用

# 配置日志系统
def setup_logging():
    """统一配置日志系统"""
    log_level = getattr(logging, config.system.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stderr)]
    )
    
    # 设置第三方库日志级别
    for logger_name in ["httpcore.connection", "httpcore.http11", "httpx", "openai._base_client", "asyncio"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

setup_logging()
logger = logging.getLogger("NagaConversation")

# 全局状态管理
class SystemState:
    """系统状态管理器"""
    _tree_thinking_initialized = False
    _mcp_services_initialized = False
    _voice_enabled_logged = False
    _memory_initialized = False
    _persistent_context_initialized = False

# GRAG记忆系统导入
def init_memory_manager():
    """初始化GRAG记忆系统"""
    if not config.grag.enabled:
        return None
    
    try:
        from summer_memory.memory_manager import memory_manager
        print("[GRAG] ✅ 夏园记忆系统初始化成功")
        return memory_manager
    except Exception as e:
        logger.error(f"夏园记忆系统加载失败: {e}")
        return None

memory_manager = init_memory_manager()

# 工具函数
def now():
    """获取当前时间戳"""
    return time.strftime('%H:%M:%S:') + str(int(time.time() * 1000) % 10000)

_builtin_print = print
def print(*a, **k):
    """自定义打印函数"""
    return sys.stderr.write('[print] ' + (' '.join(map(str, a))) + '\n')

class NagaConversation:  # 对话主类
    def __init__(self):
        self.mcp = get_mcp_manager()
        self.messages = []
        self.dev_mode = False
        # ✅ 修正：去掉多余的 '/'，避免 .../v1//chat/completions
        self.async_client = AsyncOpenAI(
            api_key=config.api.api_key,
            base_url=config.api.base_url.rstrip('/')
        )
        
        # 初始化MCP服务系统
        self._init_mcp_services()
        
        # 初始化GRAG记忆系统（只在首次初始化时显示日志）
        self.memory_manager = memory_manager
        if self.memory_manager and not SystemState._memory_initialized:
            logger.info("夏园记忆系统已初始化")
            SystemState._memory_initialized = True
        
        # 初始化持久化上下文（只在首次初始化时显示日志）
        if config.api.persistent_context and not SystemState._persistent_context_initialized:
            self._load_persistent_context()
            SystemState._persistent_context_initialized = True
        
        # 初始化语音处理系统
        self.voice = None
        if config.system.voice_enabled:
            try:
                if not SystemState._voice_enabled_logged:
                    logger.info("语音功能已启用（语音输入+输出），由UI层管理")
                    SystemState._voice_enabled_logged = True
            except Exception as e:
                logger.warning(f"语音系统初始化失败: {e}")
                self.voice = None
        
        # 禁用树状思考系统
        self.tree_thinking = None

    def _load_persistent_context(self):
        """从日志文件加载历史对话上下文"""
        if not config.api.context_parse_logs:
            return
            
        try:
            from logs.log_context_parser import get_log_parser
            parser = get_log_parser()
            
            # 计算最大消息数量
            max_messages = config.api.max_history_rounds * 2
            
            # 加载历史对话
            recent_messages = parser.load_recent_context(
                days=config.api.context_load_days,
                max_messages=max_messages
            )
            
            if recent_messages:
                self.messages = recent_messages
                logger.info(f"✅ 从日志文件加载了 {len(self.messages)} 条历史对话")
                
                # 显示统计信息
                stats = parser.get_context_statistics(config.api.context_load_days)
                logger.info(f"📊 上下文统计: {stats['total_files']}个文件, {stats['total_messages']}条消息")
            else:
                logger.info("📝 未找到历史对话记录，将开始新的对话")
                
        except ImportError:
            logger.warning("⚠️ 日志解析器模块未找到，跳过持久化上下文加载")
        except Exception as e:
            logger.error(f"❌ 加载持久化上下文失败: {e}")
            # 失败时不影响正常使用，继续使用空上下文

    def _init_mcp_services(self):
        """初始化MCP服务系统（只在首次初始化时输出日志，后续静默）"""
        if SystemState._mcp_services_initialized:
            # 静默跳过，不输出任何日志
            return
        try:
            # 自动注册所有MCP服务和handoff
            self.mcp.auto_register_services()
            logger.info("MCP服务系统初始化完成")
            SystemState._mcp_services_initialized = True
            
            # 异步启动NagaPortal自动登录
            self._start_naga_portal_auto_login()
            
            # 异步启动物联网通讯连接状态检查
            self._start_mqtt_status_check()
        except Exception as e:
            logger.error(f"MCP服务系统初始化失败: {e}")
    
    def _start_naga_portal_auto_login(self):
        """启动NagaPortal自动登录（异步）"""
        try:
            # 检查是否配置了NagaPortal
            if not config.naga_portal.username or not config.naga_portal.password:
                return  # 静默跳过，不输出日志
            
            # 在新线程中异步执行登录
            def run_auto_login():
                try:
                    import sys
                    import os
                    # 添加项目根目录到Python路径
                    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    sys.path.insert(0, project_root)
                    
                    from mcpserver.agent_naga_portal.portal_login_manager import auto_login_naga_portal
                    
                    # 创建新的事件循环
                    import asyncio
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    try:
                        # 执行自动登录
                        result = loop.run_until_complete(auto_login_naga_portal())
                        
                        if result['success']:
                            # 登录成功，显示状态
                            print("✅ NagaPortal自动登录成功")
                            self._show_naga_portal_status()
                        else:
                            # 登录失败，显示错误
                            error_msg = result.get('message', '未知错误')
                            print(f"❌ NagaPortal自动登录失败: {error_msg}")
                            self._show_naga_portal_status()
                    finally:
                        loop.close()
                        
                except Exception as e:
                    # 登录异常，显示错误
                    print(f"❌ NagaPortal自动登录异常: {e}")
                    self._show_naga_portal_status()
            
            # 启动后台线程
            import threading
            login_thread = threading.Thread(target=run_auto_login, daemon=True)
            login_thread.start()
            
        except Exception as e:
            # 启动异常，显示错误
            print(f"❌ NagaPortal自动登录启动失败: {e}")
            self._show_naga_portal_status()

    def _show_naga_portal_status(self):
        """显示NagaPortal状态（登录完成后调用）"""
        try:
            from mcpserver.agent_naga_portal.portal_login_manager import get_portal_login_manager
            login_manager = get_portal_login_manager()
            status = login_manager.get_status()
            cookies = login_manager.get_cookies()
            
            print(f"🌐 NagaPortal状态:")
            print(f"   地址: {config.naga_portal.portal_url}")
            print(f"   用户: {config.naga_portal.username[:3]}***{config.naga_portal.username[-3:] if len(config.naga_portal.username) > 6 else '***'}")
            
            if cookies:
                print(f"🍪 Cookie信息 ({len(cookies)}个):")
                for name, value in cookies.items():
                    print(f"   {name}: {value}")
            else:
                print(f"🍪 Cookie: 未获取到")
            
            user_id = status.get('user_id')
            if user_id:
                print(f"👤 用户ID: {user_id}")
            else:
                print(f"👤 用户ID: 未获取到")
                
            # 显示登录状态
            if status.get('is_logged_in'):
                print(f"✅ 登录状态: 已登录")
            else:
                print(f"❌ 登录状态: 未登录")
                if status.get('login_error'):
                    print(f"   错误: {status.get('login_error')}")
                    
        except Exception as e:
            print(f"🍪 NagaPortal状态获取失败: {e}")
    
    def _start_mqtt_status_check(self):
        """启动物联网通讯连接并显示状态（异步）"""
        try:
            # 检查是否配置了物联网通讯
            if not config.mqtt.enabled:
                return  # 静默跳过，不输出日志
            
            # 在新线程中异步执行物联网通讯连接
            def run_mqtt_connection():
                try:
                    import sys
                    import os
                    import time
                    # 添加项目根目录到Python路径
                    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    sys.path.insert(0, project_root)
                    
                    try:
                        from mqtt_tool.device_switch import device_manager
                        
                        # 尝试连接物联网设备
                        if hasattr(device_manager, 'connect'):
                            success = device_manager.connect()
                            if success:
                                print("🔗 物联网通讯状态: 已连接")
                            else:
                                print("⚠️ 物联网通讯状态: 连接失败（将在使用时重试）")
                        else:
                            print("❌ 物联网通讯功能不可用")
                            
                    except Exception as e:
                        print(f"⚠️ 物联网通讯连接失败: {e}")
                        
                except Exception as e:
                    print(f"❌ 物联网通讯连接异常: {e}")
            
            # 启动后台线程
            import threading
            mqtt_thread = threading.Thread(target=run_mqtt_connection, daemon=True)
            mqtt_thread.start()
            
        except Exception as e:
            print(f"❌ 物联网通讯连接启动失败: {e}")
    
    def save_log(self, u, a):  # 保存对话日志
        if self.dev_mode:
            return  # 开发者模式不写日志
        d = datetime.now().strftime('%Y-%m-%d')
        t = datetime.now().strftime('%H:%M:%S')
        
        # 确保日志目录存在
        log_dir = config.system.log_dir
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
            logger.info(f"已创建日志目录: {log_dir}")
        
        # 保存对话日志
        log_file = os.path.join(log_dir, f"{d}.log")
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"[{t}] 用户: {u}\n")
                f.write(f"[{t}] {AI_NAME}: {a}\n")
                f.write("-" * 50 + "\n")
        except Exception as e:
            logger.error(f"保存日志失败: {e}")
    
    def _format_services_for_prompt(self, available_services: dict) -> str:
        """格式化可用服务列表为prompt字符串，MCP服务和Agent服务分开，包含具体调用格式"""
        mcp_services = available_services.get("mcp_services", [])
        agent_services = available_services.get("agent_services", [])
        
        # 获取本地城市信息和当前时间
        local_city = "未知城市"
        current_time = ""
        try:
            # 从WeatherTimeAgent获取本地城市信息
            from mcpserver.agent_weather_time.agent_weather_time import WeatherTimeTool
            weather_tool = WeatherTimeTool()
            local_city = getattr(weather_tool, '_local_city', '未知城市') or '未知城市'
            
            # 获取当前时间
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            print(f"[DEBUG] 获取本地信息失败: {e}")
        
        # 格式化MCP服务列表，包含具体调用格式
        mcp_list = []
        for service in mcp_services:
            name = service.get("name", "")
            description = service.get("description", "")
            display_name = service.get("display_name", name)
            tools = service.get("available_tools", [])
            
            # 展示name+displayName
            if description:
                mcp_list.append(f"- {name}: {description}")
            else:
                mcp_list.append(f"- {name}")
            
            # 为每个工具显示具体调用格式
            if tools:
                for tool in tools:
                    tool_name = tool.get('name', '')
                    tool_desc = tool.get('description', '')
                    tool_example = tool.get('example', '')
                    
                    if tool_name and tool_example:
                        # 解析示例JSON，提取参数
                        try:
                            import json
                            example_data = json.loads(tool_example)
                            params = []
                            for key, value in example_data.items():
                                if key != 'tool_name':
                                    params.append(f"{key}: {value}")  # 不再需要对天气进行特殊处理
                            
                            # 构建调用格式
                            format_str = f"  {tool_name}: ｛\n"
                            format_str += f"    \"agentType\": \"mcp\",\n"
                            format_str += f"    \"service_name\": \"{name}\",\n"
                            format_str += f"    \"tool_name\": \"{tool_name}\",\n"
                            for param in params:
                                # 将中文参数名转换为英文
                                param_key, param_value = param.split(': ', 1)
                                format_str += f"    \"{param_key}\": \"{param_value}\",\n"
                            format_str += f"  ｝\n"
                            
                            mcp_list.append(format_str)
                        except:
                            # 如果JSON解析失败，使用简单格式
                            mcp_list.append(f"  {tool_name}: 使用tool_name参数调用")
        
        # 格式化Agent服务列表
        agent_list = []
        
        # 1. 添加handoff服务
        for service in agent_services:
            name = service.get("name", "")
            description = service.get("description", "")
            tool_name = service.get("tool_name", "agent")
            display_name = service.get("display_name", name)
            # 展示name+displayName
            if description:
                agent_list.append(f"- {name}(工具名: {tool_name}): {description}")
            else:
                agent_list.append(f"- {name}(工具名: {tool_name})")
        
        # 2. 直接从AgentManager获取已注册的Agent
        try:
            from mcpserver.agent_manager import get_agent_manager
            agent_manager = get_agent_manager()
            agent_manager_agents = agent_manager.get_available_agents()
            
            for agent in agent_manager_agents:
                name = agent.get("name", "")
                base_name = agent.get("base_name", "")
                description = agent.get("description", "")
                
                # 展示格式：base_name: 描述
                if description:
                    agent_list.append(f"- {base_name}: {description}")
                else:
                    agent_list.append(f"- {base_name}")
                    
        except Exception as e:
            # 如果AgentManager不可用，静默处理
            pass
        
        # 添加本地信息说明
        local_info = f"\n\n【当前环境信息】\n- 本地城市: {local_city}\n- 当前时间: {current_time}\n\n【使用说明】\n- 天气/时间查询时，请使用上述本地城市信息作为city参数\n- 所有时间相关查询都基于当前系统时间"
        
        # 返回格式化的服务列表
        result = {
            "available_mcp_services": "\n".join(mcp_list) + local_info if mcp_list else "无" + local_info,
            "available_agent_services": "\n".join(agent_list) if agent_list else "无"
        }
        
        return result

    async def process(self, u, is_voice_input=False):  # 添加is_voice_input参数
        try:
            # 开发者模式优先判断
            if u.strip().lower() == "#devmode":
                self.dev_mode = not self.dev_mode  # 切换模式
                status = "进入" if self.dev_mode else "退出"
                yield (AI_NAME, f"已{status}开发者模式")
                return

            # 只在语音输入时显示处理提示
            if is_voice_input:
                print(f"开始处理用户输入：{now()}")  # 语音转文本结束，开始处理
                     
            # 获取过滤后的服务列表
            available_services = self.mcp.get_available_services_filtered()
            services_text = self._format_services_for_prompt(available_services)
            
            # 添加handoff提示词 - 先获取服务信息再格式化
            system_prompt = f"{RECOMMENDED_PROMPT_PREFIX}\n{config.prompts.naga_system_prompt.format(ai_name=AI_NAME, **services_text)}"
            
            # 使用消息管理器统一的消息拼接逻辑（UI界面使用）
            from apiserver.message_manager import message_manager
            msgs = message_manager.build_conversation_messages_from_memory(
                memory_messages=self.messages,
                system_prompt=system_prompt,
                current_message=u,
                max_history_rounds=config.api.max_history_rounds
            )

            print(f"GTP请求发送：{now()}")  # AI请求前
            
            # 流式处理：实时检测工具调用，使用统一的工具调用循环
            try:
                # 导入流式工具调用提取器
                from apiserver.streaming_tool_extractor import StreamingToolCallExtractor
                import queue
                
                # 创建工具调用队列
                tool_calls_queue = queue.Queue()
                tool_extractor = StreamingToolCallExtractor(self.mcp)
                
                # 用于累积前端显示的纯文本（不包含工具调用）
                display_text = ""
                
                # 设置回调函数
                def on_text_chunk(text: str, chunk_type: str):
                    """处理文本块 - 发送到前端显示"""
                    if chunk_type == "chunk":
                        nonlocal display_text
                        display_text += text
                        return (AI_NAME, text)
                    return None
                
                def on_sentence(sentence: str, sentence_type: str):
                    """处理完整句子"""
                    if sentence_type == "sentence":
                        print(f"完成句子: {sentence}")
                    return None
                
                def on_tool_result(result: str, result_type: str):
                    """处理工具结果 - 不发送到前端"""
                    if result_type == "tool_result":
                        print(f"✅ 工具执行完成: {result[:100]}...")
                    elif result_type == "tool_error":
                        print(f"❌ 工具执行错误: {result}")
                    return None
                
                # 设置回调
                tool_extractor.set_callbacks(
                    on_text_chunk=on_text_chunk,
                    on_sentence=on_sentence,
                    on_tool_result=on_tool_result,
                    tool_calls_queue=tool_calls_queue
                )
                
                # 调用LLM API - 流式模式
                resp = await self.async_client.chat.completions.create(
                    model=config.api.model,
                    messages=msgs,
                    temperature=config.api.temperature,
                    max_tokens=config.api.max_tokens,
                    stream=True
                )
                
                # === 新增：流式兜底开关 ===
                saw_any_chunks = False

                # 处理流式响应
                async for chunk in resp:
                    # 原始增量日志（AI 原始输出）
                    try:
                        delta = getattr(chunk.choices[0], 'delta', None) if chunk.choices else None
                        if delta is not None:
                            logger.info("openai.delta: %r", getattr(delta, 'content', None))
                    except Exception:
                        pass

                    # 安全检查：确保chunk.choices不为空且有内容
                    if (chunk.choices and 
                        len(chunk.choices) > 0 and 
                        hasattr(chunk.choices[0], 'delta') and 
                        chunk.choices[0].delta.content):
                        content = chunk.choices[0].delta.content
                        saw_any_chunks = True  # ✅ 收到增量
                        # 使用流式工具调用提取器处理内容
                        results = await tool_extractor.process_text_chunk(content)
                        if results:
                            for result in results:
                                if isinstance(result, tuple) and len(result) == 2:
                                    yield result
                                elif isinstance(result, str):
                                    yield (AI_NAME, result)
                
                # 完成处理（先收尾一次）
                final_results = await tool_extractor.finish_processing()
                if final_results:
                    for result in final_results:
                        if isinstance(result, tuple) and len(result) == 2:
                            yield result
                        elif isinstance(result, str):
                            yield (AI_NAME, result)

                # === 兜底：整段流式未收到任何增量，切一次非流式拿完整答案 ===
                if not saw_any_chunks:
                    try:
                        logger.warning("流式未收到任何增量，切换一次非流式兜底。")
                        non_stream_resp = await self.async_client.chat.completions.create(
                            model=config.api.model,
                            messages=msgs,
                            temperature=config.api.temperature,
                            max_tokens=config.api.max_tokens,
                            stream=False
                        )
                        non_stream_text = non_stream_resp.choices[0].message.content or ""
                        if non_stream_text:
                            results = await tool_extractor.process_text_chunk(non_stream_text)
                            if results:
                                for result in results:
                                    if isinstance(result, tuple) and len(result) == 2:
                                        yield result
                                    elif isinstance(result, str):
                                        yield (AI_NAME, result)
                            # 再次收尾
                            final_results = await tool_extractor.finish_processing()
                            if final_results:
                                for result in final_results:
                                    if isinstance(result, tuple) and len(result) == 2:
                                        yield result
                                    elif isinstance(result, str):
                                        yield (AI_NAME, result)
                    except Exception as e:
                        logger.error(f"非流式兜底失败: {e}")
                
                # 检查是否有工具调用需要处理
                if not tool_calls_queue.empty():
                    # 使用统一的工具调用循环处理
                    async def llm_caller(messages, use_stream=False):
                        """LLM调用函数，用于工具调用循环"""
                        # 这里不需要实际调用LLM，因为工具调用已经提取完成
                        return {'content': '', 'status': 'success'}
                    
                    # 使用工具调用循环处理工具调用
                    result = await tool_call_loop(msgs, self.mcp, llm_caller, is_streaming=True, tool_calls_queue=tool_calls_queue)
                    
                    if result.get('has_tool_results'):
                        # 有工具执行结果，让LLM继续处理
                        tool_results = result['content']
                        
                        # 构建包含工具结果的消息（使用统一的消息拼接逻辑）
                        tool_messages = message_manager.build_conversation_messages_from_memory(
                            memory_messages=self.messages,
                            system_prompt=system_prompt,
                            current_message=f"工具执行结果：{tool_results}",
                            max_history_rounds=config.api.max_history_rounds
                        )
                        
                        # 调用LLM继续处理工具结果（保持流式）
                        try:
                            resp2 = await self.async_client.chat.completions.create(
                                model=config.api.model,
                                messages=tool_messages,
                                temperature=config.api.temperature,
                                max_tokens=config.api.max_tokens,
                                stream=True
                            )
                            
                            # 处理LLM的继续响应 - 也需要通过流式工具调用提取器处理
                            async for chunk in resp2:
                                # 安全检查：确保chunk.choices不为空且有内容
                                if (chunk.choices and 
                                    len(chunk.choices) > 0 and 
                                    hasattr(chunk.choices[0], 'delta') and 
                                    chunk.choices[0].delta.content):
                                    content = chunk.choices[0].delta.content
                                    # 使用流式工具调用提取器处理内容
                                    results = await tool_extractor.process_text_chunk(content)
                                    if results:
                                        for result in results:
                                            if isinstance(result, tuple) and len(result) == 2:
                                                yield result
                                            elif isinstance(result, str):
                                                yield (AI_NAME, result)
                        except Exception as e:
                            print(f"LLM继续处理工具结果失败: {e}")
                
                # 再次收尾（保证完全清空缓冲）
                final_results = await tool_extractor.finish_processing()
                if final_results:
                    for result in final_results:
                        if isinstance(result, tuple) and len(result) == 2:
                            yield result
                        elif isinstance(result, str):
                            yield (AI_NAME, result)
                
                # 保存对话历史（使用前端显示的纯文本）
                print(f"[DEBUG] 最终display_text长度: {len(display_text)}")
                print(f"[DEBUG] 最终display_text内容: {display_text[:200]}...")
                self.messages += [{"role": "user", "content": u}, {"role": "assistant", "content": display_text}]
                self.save_log(u, display_text)
                
                # GRAG记忆存储（开发者模式不写入）- 使用前端显示的纯文本
                if self.memory_manager and not self.dev_mode:
                    try:
                        # 使用前端显示的纯文本进行五元组提取
                        await self.memory_manager.add_conversation_memory(u, display_text)
                    except Exception as e:
                        logger.error(f"GRAG记忆存储失败: {e}")
                
            except Exception as e:
                print(f"工具调用循环失败: {e}")
                # 区分API错误和MCP错误
                if "API" in str(e) or "api" in str(e) or "HTTP" in str(e) or "连接" in str(e):
                    yield (AI_NAME, f"[API调用异常]: {e}")
                else:
                    yield (AI_NAME, f"[MCP服务异常]: {e}")
                return

            return
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            # 区分API错误和MCP错误
            if "API" in str(e) or "api" in str(e) or "HTTP" in str(e) or "连接" in str(e):
                yield (AI_NAME, f"[API调用异常]: {e}")
            else:
                yield (AI_NAME, f"[MCP服务异常]: {e}")
            return

    async def get_response(self, prompt: str, temperature: float = 0.7) -> str:
        """为树状思考系统等提供API调用接口"""  # 统一接口
        try:
            response = await self.async_client.chat.completions.create(
                model=config.api.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=config.api.max_tokens
            )
            return response.choices[0].message.content
        except RuntimeError as e:
            if "handler is closed" in str(e):
                logger.debug(f"忽略连接关闭异常，重新创建客户端: {e}")
                # ✅ 重新创建客户端并重试（同样去掉末尾 '/'）
                self.async_client = AsyncOpenAI(
                    api_key=config.api.api_key,
                    base_url=config.api.base_url.rstrip('/')
                )
                response = await self.async_client.chat.completions.create(
                    model=config.api.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    max_tokens=config.api.max_tokens
                )
                return response.choices[0].message.content
            else:
                logger.error(f"API调用失败: {e}")
                return f"API调用出错: {str(e)}"
        except Exception as e:
            logger.error(f"API调用失败: {e}")
            return f"API调用出错: {str(e)}"

async def process_user_message(s, msg):
    if config.system.voice_enabled and not msg:  # 无文本输入时启动语音识别
        async for text in s.voice.stt_stream():
            if text:
                msg = text
                break
        return await s.process(msg, is_voice_input=True)  # 语音输入
    return await s.process(msg, is_voice_input=False)  # 文字输入
