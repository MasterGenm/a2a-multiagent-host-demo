import logging
import asyncio
import traceback
from typing import List, Dict, Optional, Tuple

from system.config import config, AI_NAME

# 任务管理器（异步五元组提取）
from .task_manager import task_manager, start_task_manager, start_auto_cleanup

# 五元组提取与图谱存储
from .quintuple_extractor import extract_quintuples
from .quintuple_graph import (
    store_quintuples,
    query_graph_by_keywords,
    get_all_quintuples,
)

# DeepSeek RAG 组合查询
try:
    from .quintuple_rag_query import query_knowledge, set_context
except Exception as e:  # 启动时缺依赖也要继续跑
    query_knowledge = None        # type: ignore
    set_context = None            # type: ignore
    logging.getLogger(__name__).warning(
        "[GRAG] 导入 quintuple_rag_query 失败，将仅使用轻量对话记忆: %s", e
    )

logger = logging.getLogger(__name__)


class GRAGMemoryManager:
    """
    GRAG 知识图谱记忆管理器（适配 TradingAgents 项目）

    功能拆分：
    1）写入：把「用户输入 + 模型回复」组成一段文本，异步提取五元组并写入 JSON / Neo4j
    2）读取：优先用 RAG+图谱查询记忆，失败时退回 recent_context（轻量记忆）
    """

    def __init__(self) -> None:
        # ---- 读取配置，增加兜底 ----
        try:
            grag_cfg = config.grag
            self.enabled: bool = getattr(grag_cfg, "enabled", True)
            self.auto_extract: bool = getattr(grag_cfg, "auto_extract", True)
            self.context_length: int = getattr(grag_cfg, "context_length", 6)
            self.similarity_threshold: float = getattr(
                grag_cfg, "similarity_threshold", 0.4
            )
        except Exception as e:
            logger.warning("[GRAG] 读取 config.grag 失败，暂时关闭 GRAG 记忆: %s", e)
            self.enabled = False
            self.auto_extract = False
            self.context_length = 6
            self.similarity_threshold = 0.4

        # 轻量对话上下文（始终可用，不依赖图谱）
        self.recent_context: List[str] = []
        self.extraction_cache = set()   # 避免重复提取
        self.active_tasks = set()       # 正在处理的任务 ID 集合

        # 调试字段：最近一次写入状态
        self.last_write_ok: bool = False
        self.last_write_preview: str = ""

        if not self.enabled:
            logger.info("[GRAG] 已禁用（config.grag.enabled = False），仅使用最近对话作为上下文")
            return

        logger.info(
            "[GRAG] 记忆系统初始化: enabled=%s auto_extract=%s context_length=%s",
            self.enabled,
            self.auto_extract,
            self.context_length,
        )

        # 任务完成 / 失败回调（同步函数即可）
        task_manager.on_task_completed = self._on_task_completed
        task_manager.on_task_failed = self._on_task_failed

        # 启动自动清理已完成任务（在 task_manager 内部维护）
        try:
            start_auto_cleanup()
        except Exception as e:
            logger.warning("[GRAG] 启动自动清理任务失败（可忽略）: %s", e)

    # ------------------------------------------------------------------
    # 写入：把一轮对话写入记忆
    # ------------------------------------------------------------------
    async def add_conversation_memory(self, user_input: str, ai_response: str) -> bool:
        """
        把「本轮 user + AI」写入记忆系统：
        - recent_context：始终更新
        - 如果 auto_extract=True：提交五元组提取任务，落到 JSON/Neo4j
        """
        # 先处理最近上下文，无论 GRAG 开没开都写
        conversation_text = f"用户: {user_input}\n{AI_NAME}: {ai_response}"
        self.recent_context.append(conversation_text)
        if len(self.recent_context) > self.context_length:
            self.recent_context = self.recent_context[-self.context_length :]

        # 重置调试字段
        self.last_write_ok = False
        self.last_write_preview = ""

        if not self.enabled:
            # 只用轻量上下文，不触发图谱
            self.last_write_ok = True
            self.last_write_preview = conversation_text[:200]
            return True

        # GRAG 开启：尝试异步五元组提取
        if not self.auto_extract:
            logger.info("[GRAG] auto_extract=False，仅更新 recent_context，不写图谱")
            self.last_write_ok = True
            self.last_write_preview = conversation_text[:200]
            return True

        try:
            # 确保任务管理器已启动
            try:
                await start_task_manager()
            except RuntimeError as e:
                logger.warning("[GRAG] 启动任务管理器失败，将退回同步提取: %s", e)
                return await self._extract_and_store_fallback(conversation_text)

            # 提交任务
            task_id = await task_manager.add_task(conversation_text)
            self.active_tasks.add(task_id)
            logger.info("[GRAG] 已提交五元组提取任务: %s", task_id)

            # 只要任务成功提交，就认为写入链路已启动
            self.last_write_ok = True
            self.last_write_preview = conversation_text[:200]
            return True

        except Exception as e:
            logger.error("[GRAG] 提交提取任务失败，将退回同步提取: %s", e)
            return await self._extract_and_store_fallback(conversation_text)

    # ------------------------------------------------------------------
    # TaskManager 回调（由 _worker_loop 调用，注意是同步函数）
    # ------------------------------------------------------------------
    def _on_task_completed(self, task_id: str, quintuples: List[Tuple[str, str, str, str, str]]) -> None:
        """任务完成：把五元组落到 JSON / Neo4j 中"""
        try:
            self.active_tasks.discard(task_id)
            if not quintuples:
                logger.info("[GRAG] 任务 %s 未提取到五元组", task_id)
                return

            logger.info("[GRAG] 任务 %s 提取到 %d 个五元组，开始存储", task_id, len(quintuples))
            ok = store_quintuples(quintuples)
            if ok:
                logger.info("[GRAG] 任务 %s 的五元组存储成功", task_id)
            else:
                logger.error("[GRAG] 任务 %s 的五元组存储失败", task_id)
        except Exception as e:  # 保底别让异常炸掉 worker
            logger.error("[GRAG] 任务完成回调异常: %s", e)
            logger.error(traceback.format_exc())

    def _on_task_failed(self, task_id: str, error: str) -> None:
        """任务失败"""
        self.active_tasks.discard(task_id)
        logger.error("[GRAG] 任务 %s 失败: %s", task_id, error)

    # ------------------------------------------------------------------
    # 回退方案：不依赖 task_manager 的同步提取 + 存储
    # ------------------------------------------------------------------
    async def _extract_and_store_fallback(self, text: str) -> bool:
        """
        在 task_manager 异常时，直接在当前协程里完成：
        1）extract_quintuples（线程池）
        2）store_quintuples（线程池）
        """
        import hashlib

        try:
            text_hash = hashlib.sha256(text.encode()).hexdigest()
            if text_hash in self.extraction_cache:
                logger.info("[GRAG] 文本已处理过，跳过重复提取")
                self.last_write_ok = True
                self.last_write_preview = text[:200]
                return True

            logger.info("[GRAG] 使用回退方案提取五元组...")
            quintuples = await asyncio.to_thread(extract_quintuples, text)

            if not quintuples:
                logger.info("[GRAG] 回退方案未提取到任何五元组")
                # 依然算「链路正常结束」
                self.last_write_ok = True
                self.last_write_preview = text[:200]
                return True

            logger.info("[GRAG] 回退方案提取到 %d 个五元组，开始存储", len(quintuples))
            ok = await asyncio.to_thread(store_quintuples, quintuples)
            if ok:
                self.extraction_cache.add(text_hash)
                self.last_write_ok = True
                self.last_write_preview = text[:200]
                logger.info("[GRAG] 回退方案存储成功")
                return True
            else:
                logger.error("[GRAG] 回退方案存储失败")
                self.last_write_ok = False
                return False

        except Exception as e:
            logger.error("[GRAG] 回退方案执行失败: %s", e)
            logger.error(traceback.format_exc())
            self.last_write_ok = False
            return False

    # ------------------------------------------------------------------
    # 读取：先查图谱/RAG，再退回 recent_context
    # ------------------------------------------------------------------
    async def query_memory(self, question: str) -> Optional[str]:
        """
        记忆查询主入口：
        1）如果 query_knowledge 可用，则：
           - 把 recent_context 传给 set_context，帮助生成检索关键词
           - 调用 query_knowledge(question)
           - 若命中有效答案则直接返回
        2）否则或未命中，则退回 recent_context（最近若干轮对话拼接）
        """
        if not self.enabled:
            return None

        try:
            logger.info("[GRAG] 开始记忆查询: %r", question)

            # 传入最近上下文，帮助 RAG 生成更合理的关键词
            if set_context is not None:
                try:
                    set_context(self.recent_context)
                except Exception as e:
                    logger.warning("[GRAG] set_context 调用失败: %s", e)

            # 1) 图谱 + RAG 查询
            if query_knowledge is not None:
                try:
                    rag_result = await asyncio.to_thread(query_knowledge, question)
                    if rag_result and "未在知识图谱中找到相关信息" not in str(rag_result):
                        logger.info("[GRAG] RAG 记忆命中")
                        return str(rag_result)
                except Exception as e:
                    logger.warning("[GRAG] query_knowledge 调用失败，将退回轻量记忆: %s", e)

            # 2) 退回轻量记忆（recent_context）
            if self.recent_context:
                fallback = "\n".join(self.recent_context[-self.context_length :])
                logger.info("[GRAG] 使用 recent_context 作为记忆，共 %d 条", len(self.recent_context))
                return fallback

            logger.info("[GRAG] 没有可用记忆（图谱+轻量记忆均为空）")
            return None

        except Exception as e:
            logger.error("[GRAG] query_memory 异常: %s", e)
            logger.error(traceback.format_exc())

            # 出错时，仍尽量退回 recent_context，避免整个链路报错
            if self.recent_context:
                return "\n".join(self.recent_context[-self.context_length :])
            return None

    # ------------------------------------------------------------------
    # 直接获取相关五元组（用于调试 / 可视化）
    # ------------------------------------------------------------------
    async def get_relevant_memories(
        self, query: str, limit: int = 3
    ) -> List[Tuple[str, str, str, str, str]]:
        if not self.enabled:
            return []

        try:
            quintuples = await asyncio.to_thread(query_graph_by_keywords, [query])
            return quintuples[:limit]
        except Exception as e:
            logger.error("[GRAG] 获取相关记忆失败: %s", e)
            logger.error(traceback.format_exc())
            return []

    def get_memory_stats(self) -> Dict:
        """
        获取记忆统计信息，便于在 UI / 日志中查看。
        不依赖 main.py，可选使用。
        """
        if not self.enabled:
            return {"enabled": False}

        try:
            all_quintuples = get_all_quintuples()
            try:
                task_stats = task_manager.get_stats()
            except Exception:
                task_stats = {}

            return {
                "enabled": True,
                "total_quintuples": len(all_quintuples),
                "context_length": len(self.recent_context),
                "cache_size": len(self.extraction_cache),
                "active_tasks": len(self.active_tasks),
                "task_manager": task_stats,
                "last_write_ok": self.last_write_ok,
                "last_write_preview": self.last_write_preview,
            }
        except Exception as e:
            logger.error("[GRAG] 获取记忆统计失败: %s", e)
            return {"enabled": False, "error": str(e)}

    # ------------------------------------------------------------------
    # 其他辅助方法
    # ------------------------------------------------------------------
    async def clear_memory(self) -> bool:
        """
        仅清空 session 级记忆，不会删除 Neo4j / JSON 中已经写入的历史五元组。
        """
        try:
            self.recent_context.clear()
            self.extraction_cache.clear()
            # 取消所有活跃任务
            for tid in list(self.active_tasks):
                try:
                    await task_manager.cancel_task(tid)  # type: ignore[arg-type]
                except Exception:
                    pass
            self.active_tasks.clear()
            logger.info("[GRAG] 已清空 session 级记忆")
            return True
        except Exception as e:
            logger.error("[GRAG] 清空记忆失败: %s", e)
            logger.error(traceback.format_exc())
            return False


# 全局实例，供 main.py:  from summer_memory.memory_manager import memory_manager  使用
memory_manager = GRAGMemoryManager()
