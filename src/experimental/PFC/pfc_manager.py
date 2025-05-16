import time
import asyncio
import traceback
from typing import Dict, Optional

from src.common.logger_manager import get_logger
from .conversation import Conversation
from .conversation_initializer import initialize_core_components

# >>> 新增导入 <<<
from .pfc_types import ConversationState  # 导入 ConversationState

logger = get_logger("pfc_manager")


class PFCManager:
    """PFC对话管理器，负责管理所有对话实例"""

    _instance = None
    _instances: Dict[str, Conversation] = {}
    _initializing: Dict[str, bool] = {}  # 用于防止并发初始化同一个 stream_id

    @classmethod
    def get_instance(cls) -> "PFCManager":
        if cls._instance is None:
            cls._instance = PFCManager()
        return cls._instance

    async def get_or_create_conversation(self, stream_id: str, private_name: str) -> Optional[Conversation]:
        """获取或创建对话实例，并确保其启动"""

        if self._initializing.get(stream_id, False):
            logger.debug(f"[私聊][{private_name}] 会话实例正在初始化中，请稍候: {stream_id}")
            await asyncio.sleep(0.5)
            if stream_id in self._instances and self._instances[stream_id]._initialized:
                logger.debug(f"[私聊][{private_name}] 初始化已完成，返回现有实例: {stream_id}")
                return self._instances[stream_id]
            else:
                logger.warning(f"[私聊][{private_name}] 等待后实例仍未初始化完成或不存在。")
                return None

        if stream_id in self._instances:
            instance = self._instances[stream_id]
            if (
                hasattr(instance, "ignore_until_timestamp")
                and instance.ignore_until_timestamp
                and time.time() < instance.ignore_until_timestamp
            ):
                logger.debug(f"[私聊][{private_name}] 会话实例当前处于忽略状态: {stream_id}")
                return None

            if instance._initialized and instance.should_continue:
                logger.debug(f"[私聊][{private_name}] 使用现有活动会话实例: {stream_id}")
                return instance
            else:
                logger.warning(f"[私聊][{private_name}] 发现无效或已停止的旧实例，清理并重新创建: {stream_id}")
                await self._cleanup_conversation(instance)
                if stream_id in self._instances:
                    del self._instances[stream_id]
                if stream_id in self._initializing:  # 确保也从这里移除
                    del self._initializing[stream_id]

        conversation_instance: Optional[Conversation] = None
        try:
            logger.info(f"[私聊][{private_name}] 创建新的对话实例: {stream_id}")
            self._initializing[stream_id] = True

            conversation_instance = Conversation(stream_id, private_name)
            self._instances[stream_id] = conversation_instance

            # 调用初始化包装器
            await self._initialize_conversation_wrapper(conversation_instance)

            # 检查初始化结果并启动
            if conversation_instance._initialized and conversation_instance.should_continue:
                logger.info(f"[私聊][{private_name}] 初始化成功，调用 conversation.start() 启动主循环...")
                await conversation_instance.start()  # start 方法内部会创建 loop 任务
            else:
                logger.error(f"[私聊][{private_name}] 初始化未成功完成，无法启动实例 {stream_id}。")
                await self._cleanup_conversation(conversation_instance)
                if stream_id in self._instances:  # 再次检查以防万一
                    del self._instances[stream_id]
                conversation_instance = None

        except Exception as e:
            logger.error(f"[私聊][{private_name}] 创建或启动会话实例时发生严重错误: {stream_id}, 错误: {e}")
            logger.error(traceback.format_exc())
            if conversation_instance:
                await self._cleanup_conversation(conversation_instance)
            if stream_id in self._instances:
                del self._instances[stream_id]
            conversation_instance = None
        finally:
            if stream_id in self._initializing:  # 确保在 finally 中也检查
                self._initializing[stream_id] = False  # 清除初始化标记

        return conversation_instance

    async def _initialize_conversation_wrapper(self, conversation: Conversation):
        """
        (内部方法) 初始化会话实例的核心逻辑包装器。
        """
        stream_id = conversation.stream_id
        private_name = conversation.private_name
        try:
            logger.info(f"[私聊][{private_name}] Manager 开始调用 initialize_core_components(): {stream_id}")
            await initialize_core_components(conversation)

            # 检查初始化函数执行后的状态
            if conversation.state != ConversationState.INIT and conversation.state != ConversationState.ERROR:
                conversation._initialized = True
                conversation.should_continue = True
                logger.info(
                    f"[私聊][{private_name}] initialize_core_components() 调用完成，实例标记为已初始化且可继续: {stream_id}"
                )
            else:
                conversation._initialized = False
                conversation.should_continue = False
                logger.warning(
                    f"[私聊][{private_name}] initialize_core_components() 调用完成，但实例状态为 {conversation.state.name}，标记为未初始化或不可继续: {stream_id}"
                )

        except Exception as e:
            logger.error(
                f"[私聊][{private_name}] 调用 initialize_core_components() 时发生未捕获错误: {stream_id}, 错误: {e}"
            )
            logger.error(traceback.format_exc())
            conversation._initialized = False
            conversation.should_continue = False
            # >>> 修改：在捕获到异常时设置 ERROR 状态 <<<
            conversation.state = ConversationState.ERROR

    async def _cleanup_conversation(self, conversation: Conversation):
        """清理会话实例的资源"""
        if not conversation:
            return
        stream_id = conversation.stream_id
        private_name = conversation.private_name
        logger.info(f"[私聊][{private_name}] 开始清理会话实例资源: {stream_id}")
        try:
            if hasattr(conversation, "stop") and callable(conversation.stop):
                await conversation.stop()
            else:
                logger.warning(f"[私聊][{private_name}] Conversation 对象缺少 stop 方法。")

            logger.info(f"[私聊][{private_name}] 会话实例 {stream_id} 资源已清理")
        except Exception as e:
            logger.error(f"[私聊][{private_name}] 清理会话实例资源时失败: {stream_id}, 错误: {e}")
            logger.error(traceback.format_exc())

    async def get_conversation(self, stream_id: str) -> Optional[Conversation]:
        """获取已存在的会话实例 (只读)"""
        instance = self._instances.get(stream_id)
        if instance and instance._initialized and instance.should_continue:
            if (
                hasattr(instance, "ignore_until_timestamp")
                and instance.ignore_until_timestamp
                and time.time() < instance.ignore_until_timestamp
            ):
                return None
            return instance
        return None

    async def remove_conversation(self, stream_id: str):
        """移除并清理会话实例"""
        if stream_id in self._instances:
            instance_to_remove = self._instances[stream_id]
            logger.info(f"[管理器] 准备移除并清理会话实例: {stream_id}")
            try:
                del self._instances[stream_id]
                if stream_id in self._initializing:
                    del self._initializing[stream_id]
                await self._cleanup_conversation(instance_to_remove)
                logger.info(f"[管理器] 会话实例 {stream_id} 已成功移除并清理")
            except Exception as e:
                logger.error(f"[管理器] 移除或清理会话实例 {stream_id} 时失败: {e}")
                logger.error(traceback.format_exc())
        else:
            logger.warning(f"[管理器] 尝试移除不存在的会话实例: {stream_id}")
