# -*- coding: utf-8 -*-
# 兼容层：允许 `import forum_reader` 的旧代码继续工作
try:
    from service.utils.forum_reader import (
        get_latest_host_speech,
        format_host_speech_for_prompt,
        get_all_host_speeches,
        get_recent_agent_speeches,
    )
except Exception as e:
    # 最后兜底：提供无害空实现，避免阻断主流程
    print(f"[forum_reader-shim] 警告: 无法导入forum_reader模块，将跳过HOST发言读取功能. 错误详情: {repr(e)}")
    def get_latest_host_speech(*args, **kwargs): return None
    def format_host_speech_for_prompt(host_speech): return ""
    def get_all_host_speeches(*args, **kwargs): return []
    def get_recent_agent_speeches(*args, **kwargs): return []

__all__ = [
    "get_latest_host_speech",
    "format_host_speech_for_prompt",
    "get_all_host_speeches",
    "get_recent_agent_speeches",
]