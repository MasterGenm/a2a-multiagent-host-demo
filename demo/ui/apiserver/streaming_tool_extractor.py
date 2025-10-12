# ui/apiserver/streaming_tool_extractor.py
"""
异步友好的最小空实现（不解析工具），并提供“句子级 flush”：
- 实时把模型增量文本透传到前端
- 遇到句号/换行等边界时，触发一次句子级回调，提升显示完整度
"""

from typing import List, Dict, Any, Optional, Callable

SENTENCE_BOUNDARIES = ("。", "！", "？", ".", "!", "?", "\n")

class StreamingToolCallExtractor:
    def __init__(self, *args, **kwargs) -> None:
        self._buffer: List[str] = []
        self._on_text_chunk: Optional[Callable[[str, str], Any]] = None
        self._on_sentence: Optional[Callable[[str, str], Any]] = None
        self._on_tool_result: Optional[Callable[[str, str], Any]] = None
        self._tool_calls_queue = None
        self._sentence_buf: List[str] = []

    # 上游同步调用
    def set_callbacks(
        self,
        on_text_chunk: Optional[Callable[[str, str], Any]] = None,
        on_sentence: Optional[Callable[[str, str], Any]] = None,
        on_tool_result: Optional[Callable[[str, str], Any]] = None,
        tool_calls_queue=None,
        *args, **kwargs
    ) -> None:
        self._on_text_chunk = on_text_chunk
        self._on_sentence = on_sentence
        self._on_tool_result = on_tool_result
        self._tool_calls_queue = tool_calls_queue

    async def push_chunk(self, text_chunk: str, *args, **kwargs) -> None:
        if not text_chunk:
            return
        self._buffer.append(text_chunk)
        self._sentence_buf.append(text_chunk)
        # 1) 逐字透传
        if callable(self._on_text_chunk):
            try:
                self._on_text_chunk(text_chunk, "chunk")
            except Exception:
                pass
        # 2) 简单的句子边界 flush（可改善“只回一个词/空白”的观感）
        if text_chunk.endswith(SENTENCE_BOUNDARIES):
            await self._flush_sentence()

    async def _flush_sentence(self):
        if not self._sentence_buf:
            return
        sent = "".join(self._sentence_buf).strip()
        self._sentence_buf.clear()
        if sent and callable(self._on_sentence):
            try:
                self._on_sentence(sent, "sentence")
            except Exception:
                pass

    async def process_text_chunk(self, text: str, *args, **kwargs) -> List[str]:
        if not text:
            return []
        # 透传 & 句子 flush
        await self.push_chunk(text)
        return [text]

    async def finish_processing(self, *args, **kwargs) -> List[str]:
        # 收尾时把残余句子 flush 一次，并返回总文本
        await self._flush_sentence()
        buf = "".join(self._buffer).strip()
        self._buffer.clear()
        return [buf] if buf else []

    def __getattr__(self, name: str):
        async def _noop(*a, **k):
            return [] if name.startswith(("extract", "get", "final", "parse", "next", "flush")) else None
        return _noop

# 函数式接口（保持存在即可）
def extract_tool_calls_streaming(text_chunk: str) -> List[Dict[str, Any]]:
    return []

def extract_tool_calls(full_text: str) -> List[Dict[str, Any]]:
    return []

def is_tool_call(text: str) -> bool:
    return False

__all__ = [
    "StreamingToolCallExtractor",
    "extract_tool_calls_streaming",
    "extract_tool_calls",
    "is_tool_call",
]
