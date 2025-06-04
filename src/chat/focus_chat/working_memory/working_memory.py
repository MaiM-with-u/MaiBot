from typing import List, Any, Optional
import asyncio
from src.common.logger_manager import get_logger
from src.chat.focus_chat.working_memory.memory_manager import MemoryManager, MemoryItem

logger = get_logger(__name__)

# 问题是我不知道这个manager是不是需要和其他manager统一管理，因为这个manager是从属于每一个聊天流，都有自己的定时任务


class WorkingMemory:
    """
    工作记忆，负责协调和运作记忆
    从属于特定的流，用chat_id来标识
    """

    def __init__(self, chat_id: str, max_memories_per_chat: int = 10, auto_decay_interval: int = 60):
        """
        初始化工作记忆管理器

        Args:
            max_memories_per_chat: 每个聊天的最大记忆数量
            auto_decay_interval: 自动衰减记忆的时间间隔(秒)
        """
        self.memory_manager = MemoryManager(chat_id)

        # 记忆容量上限
        self.max_memories_per_chat = max_memories_per_chat

        # 自动衰减间隔
        self.auto_decay_interval = auto_decay_interval

        # 衰减任务
        self.decay_task = None

        # 启动自动衰减任务
        self._start_auto_decay()

    def _start_auto_decay(self):
        """启动自动衰减任务"""
        if self.decay_task is None:
            self.decay_task = asyncio.create_task(self._auto_decay_loop())

    async def _auto_decay_loop(self):
        """自动衰减循环"""
        while True:
            await asyncio.sleep(self.auto_decay_interval)
            try:
                await self.decay_all_memories()
            except Exception as e:
                print(f"自动衰减记忆时出错: {str(e)}")

    async def add_memory(self, content: Any, from_source: str = ""):
        """
        添加一段记忆到指定聊天

        Args:
            content: 记忆内容
            from_source: 数据来源

        Returns:
            包含记忆信息的字典
        """
        memory = await self.memory_manager.push_with_summary(content, from_source)
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
            # if memory_item.memory_strength < 5:
            #     await self.memory_manager.refine_memory(
            #         memory_id, f"由于时间过去了{self.auto_decay_interval}秒，记忆变的模糊，所以需要压缩"
            #     )

    async def merge_memory(self, memory_id1: str, memory_id2: str) -> MemoryItem:
        """合并记忆

        Args:
            memory_str: 记忆内容
        """
        return await self.memory_manager.merge_memories(
            memory_id1=memory_id1, memory_id2=memory_id2, reason="两端记忆有重复的内容"
        )

    async def shutdown(self) -> None:
        """关闭管理器，停止所有任务"""
        if self.decay_task and not self.decay_task.done():
            self.decay_task.cancel()
            try:
                await self.decay_task
            except asyncio.CancelledError:
                pass

    def get_all_memories(self) -> List[MemoryItem]:
        """
        获取所有记忆项目

        Returns:
            List[MemoryItem]: 当前工作记忆中的所有记忆项目列表
        """
        return self.memory_manager.get_all_items()
