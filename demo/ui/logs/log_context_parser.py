# ui/logs/log_context_parser.py
# 轻量级日志上下文解析器，占位以消除告警
from typing import List, Dict, Any

class _DummyParser:
    def load_recent_context(self, days: int, max_messages: int) -> List[Dict[str, Any]]:
        # 不加载历史，直接返回空
        return []

def get_log_parser() -> _DummyParser:
    return _DummyParser()
