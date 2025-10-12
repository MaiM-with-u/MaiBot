# -*- coding: utf-8 -*-
import asyncio
import random
from typing import List

from src.manager.async_task_manager import AsyncTask
from src.memory_system.Memory_chest import global_memory_chest
from src.common.logger import get_logger
from src.common.database.database_model import MemoryChest as MemoryChestModel
from src.config.config import global_config
from src.memory_system.memory_utils import get_all_titles

logger = get_logger("memory")


class MemoryManagementTask(AsyncTask):
    """记忆管理定时任务
    
    根据Memory_chest中的记忆数量与MAX_MEMORY_NUMBER的比例来决定执行频率：
    - 小于50%：每600秒执行一次
    - 大于等于50%：每300秒执行一次
    
    每次执行时随机选择一个title，执行choose_merge_target和merge_memory，
    然后删除原始记忆
    """
    
    def __init__(self):
        super().__init__(
            task_name="Memory Management Task",
            wait_before_start=10,  # 启动后等待10秒再开始
            run_interval=300  # 默认300秒间隔，会根据记忆数量动态调整
        )
        self.max_memory_number = global_config.memory.max_memory_number
    
    async def start_task(self, abort_flag: asyncio.Event):
        """重写start_task方法，支持动态调整执行间隔"""
        if self.wait_before_start > 0:
            # 等待指定时间后开始任务
            await asyncio.sleep(self.wait_before_start)

        while not abort_flag.is_set():
            await self.run()
            
            # 动态调整执行间隔
            current_interval = self._calculate_interval()
            logger.info(f"[记忆管理] 下次执行间隔: {current_interval}秒")
            
            if current_interval > 0:
                await asyncio.sleep(current_interval)
            else:
                break
    
    def _calculate_interval(self) -> int:
        """根据当前记忆数量计算执行间隔"""
        try:
            current_count = self._get_memory_count()
            percentage = current_count / self.max_memory_number
            
            if percentage < 0.5:
                # 小于50%，每600秒执行一次
                return 3600
            elif percentage < 0.7:
                # 大于等于50%，每300秒执行一次
                return 1800
            elif percentage < 0.9:
                # 大于等于70%，每120秒执行一次
                return 300
            elif percentage < 1.2:
                return 30
            else:
                return 10
            
        except Exception as e:
            logger.error(f"[记忆管理] 计算执行间隔时出错: {e}")
            return 300  # 默认300秒
    
    def _get_memory_count(self) -> int:
        """获取当前记忆数量"""
        try:
            count = MemoryChestModel.select().count()
            logger.debug(f"[记忆管理] 当前记忆数量: {count}")
            return count
        except Exception as e:
            logger.error(f"[记忆管理] 获取记忆数量时出错: {e}")
            return 0
    
    async def run(self):
        """执行记忆管理任务"""
        try:

            # 获取当前记忆数量
            current_count = self._get_memory_count()
            percentage = current_count / self.max_memory_number
            logger.info(f"当前记忆数量: {current_count}/{self.max_memory_number} ({percentage:.1%})")
            
            # 如果记忆数量为0，跳过执行
            if current_count < 10:
                return
            
            # 随机选择一个记忆标题和chat_id
            selected_title, selected_chat_id = self._get_random_memory_title()
            if not selected_title:
                logger.warning("无法获取随机记忆标题，跳过执行")
                return
            
            # 执行choose_merge_target获取相关记忆（标题与内容）
            related_titles, related_contents = await global_memory_chest.choose_merge_target(selected_title, selected_chat_id)
            if not related_titles or not related_contents:
                logger.info("无合适合并内容，跳过本次合并")
                return
            
            logger.info(f"为 [{selected_title}] 找到 {len(related_contents)} 条相关记忆:{related_titles}")
            
            # 执行merge_memory合并记忆
            merged_title, merged_content = await global_memory_chest.merge_memory(related_contents,selected_chat_id)
            if not merged_title or not merged_content:
                logger.warning("[记忆管理] 记忆合并失败，跳过删除")
                return
            
            logger.info(f"记忆合并成功，新标题: {merged_title}")
            
            # 删除原始记忆（包括选中的标题和相关的记忆标题）
            titles_to_delete = [selected_title] + related_titles
            deleted_count = self._delete_original_memories(titles_to_delete)
            logger.info(f"已删除 {deleted_count} 条原始记忆")

        except Exception as e:
            logger.error(f"[记忆管理] 执行记忆管理任务时发生错误: {e}", exc_info=True)
    
    def _get_random_memory_title(self) -> tuple[str, str]:
        """随机获取一个记忆标题和对应的chat_id"""
        try:
            # 获取所有记忆记录
            all_memories = MemoryChestModel.select()
            if not all_memories:
                return "", ""
            
            # 随机选择一个记忆
            selected_memory = random.choice(list(all_memories))
            return selected_memory.title, selected_memory.chat_id or ""
            
        except Exception as e:
            logger.error(f"[记忆管理] 获取随机记忆标题时发生错误: {e}")
            return "", ""
    
    def _delete_original_memories(self, related_titles: List[str]) -> int:
        """按标题删除原始记忆"""
        try:
            deleted_count = 0
            # 删除相关记忆（通过标题匹配）
            for title in related_titles:
                try:
                    # 通过标题查找并删除对应的记忆
                    memories_to_delete = MemoryChestModel.select().where(MemoryChestModel.title == title)
                    for memory in memories_to_delete:
                        MemoryChestModel.delete().where(MemoryChestModel.id == memory.id).execute()
                        deleted_count += 1
                        logger.debug(f"[记忆管理] 删除相关记忆: {memory.title}")
                except Exception as e:
                    logger.error(f"[记忆管理] 删除相关记忆时出错: {e}")
                    continue
            
            return deleted_count
            
        except Exception as e:
            logger.error(f"[记忆管理] 删除原始记忆时发生错误: {e}")
            return 0
