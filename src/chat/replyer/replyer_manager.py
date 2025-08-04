from typing import Dict, Optional, List, Tuple

from src.common.logger import get_logger
from src.config.api_ada_configs import TaskConfig
from src.chat.message_receive.chat_stream import ChatStream, get_chat_manager
from src.chat.replyer.default_generator import DefaultReplyer

logger = get_logger("ReplyerManager")


class ReplyerManager:
    def __init__(self):
        self._repliers: Dict[str, DefaultReplyer] = {}

    def get_replyer(
        self,
        chat_stream: Optional[ChatStream] = None,
        chat_id: Optional[str] = None,
        model_set_with_weight: Optional[List[Tuple[TaskConfig, float]]] = None,
        request_type: str = "replyer",
    ) -> Optional[DefaultReplyer]:
        """
        获取或创建回复器实例。

        model_configs 仅在首次为某个 chat_id/stream_id 创建实例时有效。
        后续调用将返回已缓存的实例，忽略 model_configs 参数。
        """
        stream_id = chat_stream.stream_id if chat_stream else chat_id
        if not stream_id:
            logger.warning("[ReplyerManager] 缺少 stream_id，无法获取回复器。")
            return None

        # 如果已有缓存实例，直接返回
        if stream_id in self._repliers:
            logger.debug(f"[ReplyerManager] 为 stream_id '{stream_id}' 返回已存在的回复器实例。")
            return self._repliers[stream_id]

        # 如果没有缓存，则创建新实例（首次初始化）
        logger.debug(f"[ReplyerManager] 为 stream_id '{stream_id}' 创建新的回复器实例并缓存。")

        target_stream = chat_stream
        if not target_stream:
            if chat_manager := get_chat_manager():
                target_stream = chat_manager.get_stream(stream_id)

        if not target_stream:
            logger.warning(f"[ReplyerManager] 未找到 stream_id='{stream_id}' 的聊天流，无法创建回复器。")
            return None

        # model_configs 只在此时（初始化时）生效
        replyer = DefaultReplyer(
            chat_stream=target_stream,
            model_set_with_weight=model_set_with_weight,  # 可以是None，此时使用默认模型
            request_type=request_type,
        )
        self._repliers[stream_id] = replyer
        return replyer


# 创建一个全局实例
replyer_manager = ReplyerManager()
