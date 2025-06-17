from typing import List, Any, Optional
import asyncio
import random
from src.common.logger_manager import get_logger
from src.chat.focus_chat.working_memory.memory_manager import MemoryManager, MemoryItem
from src.chat.message_receive.chat_stream import chat_manager
import time

logger = get_logger(__name__)

# 问题是我不知道这个manager是不是需要和其他manager统一管理，因为这个manager是从属于每一个聊天流，都有自己的定时任务


class WorkingMemory:
    """
    工作记忆，负责协调和运作记忆
    从属于特定的流，用chat_id来标识
    """

    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.is_group_chat = None  # 将在initialize时设置
        self._memory_items = []
        self._max_items = 50  # 默认最大条目数
        self._initialized = False

    async def initialize(self):
        """初始化工作记忆，确定聊天类型"""
        if self._initialized:
            return

        # 获取聊天类型
        chat_stream = await asyncio.to_thread(chat_manager.get_stream, self.chat_id)
        self.is_group_chat = chat_stream.group_info is not None

        # 根据聊天类型调整参数
        if not self.is_group_chat:
            self._max_items = 20  # 私聊保持较少的记忆条目
            self._memory_cleanup_threshold = 0.8  # 私聊更激进的清理阈值
        else:
            self._max_items = 50  # 群聊保持更多记忆条目
            self._memory_cleanup_threshold = 0.9  # 群聊较保守的清理阈值

        self._initialized = True

    async def add_memory_item(self, item: MemoryItem):
        """添加新的记忆项"""
        if not self._initialized:
            await self.initialize()

        # 检查是否需要清理
        if len(self._memory_items) >= self._max_items * self._memory_cleanup_threshold:
            await self._cleanup_old_memories()

        # 私聊时进行额外的重复检查
        if not self.is_group_chat:
            # 检查是否有相似内容，有则更新而不是添加
            for existing_item in self._memory_items:
                if self._is_similar_content(existing_item.content, item.content):
                    existing_item.update_with(item)
                    return

        self._memory_items.append(item)
        if len(self._memory_items) > self._max_items:
            self._memory_items.pop(0)

    def _is_similar_content(self, content1: str, content2: str) -> bool:
        """检查两个内容是否相似"""
        # 这里可以实现更复杂的相似度检查
        # 目前简单实现
        return content1.strip() == content2.strip()

    async def _cleanup_old_memories(self):
        """清理旧的记忆项"""
        if not self._memory_items:
            return

        if not self.is_group_chat:
            # 私聊：保留最近的对话和重要的记忆
            self._memory_items = [
                item for item in self._memory_items
                if (time.time() - item.timestamp < 3600) or item.importance > 0.7
            ]
        else:
            # 群聊：使用现有的清理逻辑
            # 按重要性和时间排序
            self._memory_items.sort(key=lambda x: (x.importance, -x.timestamp), reverse=True)
            # 保留前80%
            keep_count = int(self._max_items * 0.8)
            self._memory_items = self._memory_items[:keep_count]

    async def get_related_memory(self, query: str, limit: int = 5) -> List[MemoryItem]:
        """获取与查询相关的记忆项"""
        if not self._initialized:
            await self.initialize()

        if not self._memory_items:
            return []

        # 根据聊天类型调整搜索策略
        if not self.is_group_chat:
            # 私聊：优先考虑最近的对话
            recent_limit = min(limit * 2, len(self._memory_items))
            recent_items = self._memory_items[-recent_limit:]
            
            # 计算相关性分数
            scored_items = []
            for item in recent_items:
                score = self._calculate_relevance(query, item.content)
                if score > 0.3:  # 提高私聊的相关性阈值
                    scored_items.append((score, item))
            
            # 按分数排序并返回前limit个
            scored_items.sort(reverse=True)
            return [item for score, item in scored_items[:limit]]
        else:
            # 群聊：使用现有的搜索逻辑
            scored_items = []
            for item in self._memory_items:
                score = self._calculate_relevance(query, item.content)
                if score > 0.2:
                    scored_items.append((score, item))
            
            scored_items.sort(reverse=True)
            return [item for score, item in scored_items[:limit]]

    def _calculate_relevance(self, query: str, content: str) -> float:
        """计算查询与内容的相关性分数"""
        # 这里可以实现更复杂的相关性计算
        # 目前使用简单的包含关系检查
        query_words = set(query.split())
        content_words = set(content.split())
        common_words = query_words & content_words
        if not query_words:
            return 0.0
        return len(common_words) / len(query_words)

    async def clear_memory(self):
        """清空工作记忆"""
        self._memory_items = []

    def get_all_memories(self) -> List[MemoryItem]:
        """获取所有记忆项"""
        return self._memory_items.copy()

    def get_memory_count(self) -> int:
        """获取当前记忆项数量"""
        return len(self._memory_items)

    async def add_memory(self, content: Any, from_source: str = "", tags: Optional[List[str]] = None):
        """
        添加一段记忆到指定聊天

        Args:
            content: 记忆内容
            from_source: 数据来源
            tags: 数据标签列表

        Returns:
            包含记忆信息的字典
        """
        memory = await self.memory_manager.push_with_summary(content, from_source, tags)
        if len(self.memory_manager.get_all_items()) > self.max_memories_per_chat:
            self.remove_earliest_memory()

        return memory

    def remove_earliest_memory(self):
        """
        删除最早的记忆
        """
        return self.memory_manager.delete_earliest_memory()

    async def retrieve_memory(self, memory_id: str) -> Optional[MemoryItem]:
        """
        检索记忆

        Args:
            chat_id: 聊天ID
            memory_id: 记忆ID

        Returns:
            检索到的记忆项，如果不存在则返回None
        """
        memory_item = self.memory_manager.get_by_id(memory_id)
        if memory_item:
            memory_item.retrieval_count += 1
            memory_item.increase_strength(5)
            return memory_item
        return None

    async def decay_all_memories(self, decay_factor: float = 0.5):
        """
        对所有聊天的所有记忆进行衰减
        衰减：对记忆进行refine压缩，强度会变为原先的0.5

        Args:
            decay_factor: 衰减因子(0-1之间)
        """
        logger.debug(f"开始对所有记忆进行衰减，衰减因子: {decay_factor}")

        all_memories = self.memory_manager.get_all_items()

        for memory_item in all_memories:
            # 如果压缩完小于1会被删除
            memory_id = memory_item.id
            self.memory_manager.decay_memory(memory_id, decay_factor)
            if memory_item.memory_strength < 1:
                self.memory_manager.delete(memory_id)
                continue
            # 计算衰减量
            if memory_item.memory_strength < 5:
                await self.memory_manager.refine_memory(
                    memory_id, f"由于时间过去了{self.auto_decay_interval}秒，记忆变的模糊，所以需要压缩"
                )

    async def merge_memory(self, memory_id1: str, memory_id2: str) -> MemoryItem:
        """合并记忆

        Args:
            memory_str: 记忆内容
        """
        return await self.memory_manager.merge_memories(
            memory_id1=memory_id1, memory_id2=memory_id2, reason="两端记忆有重复的内容"
        )

    # 暂时没用，先留着
    async def simulate_memory_blur(self, chat_id: str, blur_rate: float = 0.2):
        """
        模拟记忆模糊过程，随机选择一部分记忆进行精简

        Args:
            chat_id: 聊天ID
            blur_rate: 模糊比率(0-1之间)，表示有多少比例的记忆会被精简
        """
        memory = self.get_memory(chat_id)

        # 获取所有字符串类型且有总结的记忆
        all_summarized_memories = []
        for type_items in memory._memory.values():
            for item in type_items:
                if isinstance(item.data, str) and hasattr(item, "summary") and item.summary:
                    all_summarized_memories.append(item)

        if not all_summarized_memories:
            return

        # 计算要模糊的记忆数量
        blur_count = max(1, int(len(all_summarized_memories) * blur_rate))

        # 随机选择要模糊的记忆
        memories_to_blur = random.sample(all_summarized_memories, min(blur_count, len(all_summarized_memories)))

        # 对选中的记忆进行精简
        for memory_item in memories_to_blur:
            try:
                # 根据记忆强度决定模糊程度
                if memory_item.memory_strength > 7:
                    requirement = "保留所有重要信息，仅略微精简"
                elif memory_item.memory_strength > 4:
                    requirement = "保留核心要点，适度精简细节"
                else:
                    requirement = "只保留最关键的1-2个要点，大幅精简内容"

                # 进行精简
                await memory.refine_memory(memory_item.id, requirement)
                print(f"已模糊记忆 {memory_item.id}，强度: {memory_item.memory_strength}, 要求: {requirement}")

            except Exception as e:
                print(f"模糊记忆 {memory_item.id} 时出错: {str(e)}")

    async def shutdown(self) -> None:
        """关闭管理器，停止所有任务"""
        if self.decay_task and not self.decay_task.done():
            self.decay_task.cancel()
            try:
                await self.decay_task
            except asyncio.CancelledError:
                pass
