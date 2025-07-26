"""
聊天API模块

专门负责聊天信息的查询和管理，采用标准Python包设计模式
使用方式：
    from src.plugin_system.apis import chat_api
    streams = chat_api.get_all_group_streams()
    chat_type = chat_api.get_stream_type(stream)

或者：
    from src.plugin_system.apis.chat_api import ChatManager as chat
    streams = chat.get_all_group_streams()
"""

from typing import List, Dict, Any, Optional
from enum import Enum

from src.common.logger import get_logger
from src.chat.message_receive.chat_stream import ChatStream, get_chat_manager

logger = get_logger("chat_api")


class SpecialTypes(Enum):
    """特殊枚举类型"""

    ALL_PLATFORMS = "all_platforms"


class ChatManager:
    """聊天管理器 - 专门负责聊天信息的查询和管理"""

    @staticmethod
    def get_all_streams(platform: Optional[str] | SpecialTypes = "qq") -> List[ChatStream]:
        # sourcery skip: for-append-to-extend
        """获取所有聊天流

        Args:
            platform: 平台筛选，默认为"qq", 可以使用 SpecialTypes.ALL_PLATFORMS 获取所有平台的群聊流

        Returns:
            List[ChatStream]: 聊天流列表

        Raises:
            TypeError: 如果 platform 不是字符串或 SpecialTypes 枚举类型
        """
        if not isinstance(platform, (str, SpecialTypes)):
            raise TypeError("platform 必须是字符串或是 SpecialTypes 枚举")
        streams = []
        try:
            for _, stream in get_chat_manager().streams.items():
                if platform == SpecialTypes.ALL_PLATFORMS or stream.platform == platform:
                    streams.append(stream)
            logger.debug(f"[ChatAPI] 获取到 {len(streams)} 个 {platform} 平台的聊天流")
        except Exception as e:
            logger.error(f"[ChatAPI] 获取聊天流失败: {e}")
        return streams

    @staticmethod
    def get_group_streams(platform: Optional[str] | SpecialTypes = "qq") -> List[ChatStream]:
        # sourcery skip: for-append-to-extend
        """获取所有群聊聊天流

        Args:
            platform: 平台筛选，默认为"qq", 可以使用 SpecialTypes.ALL_PLATFORMS 获取所有平台的群聊流

        Returns:
            List[ChatStream]: 群聊聊天流列表
        """
        if not isinstance(platform, (str, SpecialTypes)):
            raise TypeError("platform 必须是字符串或是 SpecialTypes 枚举")
        streams = []
        try:
            for _, stream in get_chat_manager().streams.items():
                if (platform == SpecialTypes.ALL_PLATFORMS or stream.platform == platform) and stream.group_info:
                    streams.append(stream)
            logger.debug(f"[ChatAPI] 获取到 {len(streams)} 个 {platform} 平台的群聊流")
        except Exception as e:
            logger.error(f"[ChatAPI] 获取群聊流失败: {e}")
        return streams

    @staticmethod
    def get_private_streams(platform: Optional[str] | SpecialTypes = "qq") -> List[ChatStream]:
        # sourcery skip: for-append-to-extend
        """获取所有私聊聊天流

        Args:
            platform: 平台筛选，默认为"qq", 可以使用 SpecialTypes.ALL_PLATFORMS 获取所有平台的群聊流

        Returns:
            List[ChatStream]: 私聊聊天流列表

        Raises:
            TypeError: 如果 platform 不是字符串或 SpecialTypes 枚举类型
        """
        if not isinstance(platform, (str, SpecialTypes)):
            raise TypeError("platform 必须是字符串或是 SpecialTypes 枚举")
        streams = []
        try:
            for _, stream in get_chat_manager().streams.items():
                if (platform == SpecialTypes.ALL_PLATFORMS or stream.platform == platform) and not stream.group_info:
                    streams.append(stream)
            logger.debug(f"[ChatAPI] 获取到 {len(streams)} 个 {platform} 平台的私聊流")
        except Exception as e:
            logger.error(f"[ChatAPI] 获取私聊流失败: {e}")
        return streams

    @staticmethod
    def get_group_stream_by_group_id(
        group_id: str, platform: Optional[str] | SpecialTypes = "qq"
    ) -> Optional[ChatStream]:  # sourcery skip: remove-unnecessary-cast
        """根据群ID获取聊天流

        Args:
            group_id: 群聊ID
            platform: 平台筛选，默认为"qq", 可以使用 SpecialTypes.ALL_PLATFORMS 获取所有平台的群聊流

        Returns:
            Optional[ChatStream]: 聊天流对象，如果未找到返回None

        Raises:
            ValueError: 如果 group_id 为空字符串
            TypeError: 如果 group_id 不是字符串类型或 platform 不是字符串或 SpecialTypes
        """
        if not isinstance(group_id, str):
            raise TypeError("group_id 必须是字符串类型")
        if not isinstance(platform, (str, SpecialTypes)):
            raise TypeError("platform 必须是字符串或是 SpecialTypes 枚举")
        if not group_id:
            raise ValueError("group_id 不能为空")
        try:
            for _, stream in get_chat_manager().streams.items():
                if (
                    stream.group_info
                    and str(stream.group_info.group_id) == str(group_id)
                    and stream.platform == platform
                ):
                    logger.debug(f"[ChatAPI] 找到群ID {group_id} 的聊天流")
                    return stream
            logger.warning(f"[ChatAPI] 未找到群ID {group_id} 的聊天流")
        except Exception as e:
            logger.error(f"[ChatAPI] 查找群聊流失败: {e}")
        return None

    @staticmethod
    def get_private_stream_by_user_id(
        user_id: str, platform: Optional[str] | SpecialTypes = "qq"
    ) -> Optional[ChatStream]:  # sourcery skip: remove-unnecessary-cast
        """根据用户ID获取私聊流

        Args:
            user_id: 用户ID
            platform: 平台筛选，默认为"qq", 可以使用 SpecialTypes.ALL_PLATFORMS 获取所有平台的群聊流

        Returns:
            Optional[ChatStream]: 聊天流对象，如果未找到返回None

        Raises:
            ValueError: 如果 user_id 为空字符串
            TypeError: 如果 user_id 不是字符串类型或 platform 不是字符串或 SpecialTypes
        """
        if not isinstance(user_id, str):
            raise TypeError("user_id 必须是字符串类型")
        if not isinstance(platform, (str, SpecialTypes)):
            raise TypeError("platform 必须是字符串或是 SpecialTypes 枚举")
        if not user_id:
            raise ValueError("user_id 不能为空")
        try:
            for _, stream in get_chat_manager().streams.items():
                if (
                    not stream.group_info
                    and str(stream.user_info.user_id) == str(user_id)
                    and stream.platform == platform
                ):
                    logger.debug(f"[ChatAPI] 找到用户ID {user_id} 的私聊流")
                    return stream
            logger.warning(f"[ChatAPI] 未找到用户ID {user_id} 的私聊流")
        except Exception as e:
            logger.error(f"[ChatAPI] 查找私聊流失败: {e}")
        return None

    @staticmethod
    def get_stream_type(chat_stream: ChatStream) -> str:
        """获取聊天流类型

        Args:
            chat_stream: 聊天流对象

        Returns:
            str: 聊天类型 ("group", "private", "unknown")

        Raises:
            TypeError: 如果 chat_stream 不是 ChatStream 类型
            ValueError: 如果 chat_stream 为空
        """
        if not isinstance(chat_stream, ChatStream):
            raise TypeError("chat_stream 必须是 ChatStream 类型")
        if not chat_stream:
            raise ValueError("chat_stream 不能为 None")

        if hasattr(chat_stream, "group_info"):
            return "group" if chat_stream.group_info else "private"
        return "unknown"

    @staticmethod
    def get_stream_info(chat_stream: ChatStream) -> Dict[str, Any]:
        """获取聊天流详细信息

        Args:
            chat_stream: 聊天流对象

        Returns:
            Dict[str, Any]: 聊天流信息字典

        Raises:
            TypeError: 如果 chat_stream 不是 ChatStream 类型
            ValueError: 如果 chat_stream 为空
        """
        if not chat_stream:
            raise ValueError("chat_stream 不能为 None")
        if not isinstance(chat_stream, ChatStream):
            raise TypeError("chat_stream 必须是 ChatStream 类型")

        try:
            info: Dict[str, Any] = {
                "stream_id": chat_stream.stream_id,
                "platform": chat_stream.platform,
                "type": ChatManager.get_stream_type(chat_stream),
            }

            if chat_stream.group_info:
                info.update(
                    {
                        "group_id": chat_stream.group_info.group_id,
                        "group_name": getattr(chat_stream.group_info, "group_name", "未知群聊"),
                    }
                )

            if chat_stream.user_info:
                info.update(
                    {
                        "user_id": chat_stream.user_info.user_id,
                        "user_name": chat_stream.user_info.user_nickname,
                    }
                )

            return info
        except Exception as e:
            logger.error(f"[ChatAPI] 获取聊天流信息失败: {e}")
            return {}

    @staticmethod
    def get_streams_summary() -> Dict[str, int]:
        """获取聊天流统计摘要

        Returns:
            Dict[str, int]: 包含各种统计信息的字典
        """
        try:
            all_streams = ChatManager.get_all_streams(SpecialTypes.ALL_PLATFORMS)
            group_streams = ChatManager.get_group_streams(SpecialTypes.ALL_PLATFORMS)
            private_streams = ChatManager.get_private_streams(SpecialTypes.ALL_PLATFORMS)

            summary = {
                "total_streams": len(all_streams),
                "group_streams": len(group_streams),
                "private_streams": len(private_streams),
                "qq_streams": len([s for s in all_streams if s.platform == "qq"]),
            }

            logger.debug(f"[ChatAPI] 聊天流统计: {summary}")
            return summary
        except Exception as e:
            logger.error(f"[ChatAPI] 获取聊天流统计失败: {e}")
            return {
                "total_streams": 0,
                "group_streams": 0,
                "private_streams": 0,
                "qq_streams": 0,
            }


# =============================================================================
# 模块级别的便捷函数 - 类似 requests.get(), requests.post() 的设计
# =============================================================================


def get_all_streams(platform: Optional[str] | SpecialTypes = "qq"):
    """获取所有聊天流的便捷函数"""
    return ChatManager.get_all_streams(platform)


def get_group_streams(platform: Optional[str] | SpecialTypes = "qq"):
    """获取群聊聊天流的便捷函数"""
    return ChatManager.get_group_streams(platform)


def get_private_streams(platform: Optional[str] | SpecialTypes = "qq"):
    """获取私聊聊天流的便捷函数"""
    return ChatManager.get_private_streams(platform)


def get_stream_by_group_id(group_id: str, platform: Optional[str] | SpecialTypes = "qq"):
    """根据群ID获取聊天流的便捷函数"""
    return ChatManager.get_group_stream_by_group_id(group_id, platform)


def get_stream_by_user_id(user_id: str, platform: Optional[str] | SpecialTypes = "qq"):
    """根据用户ID获取私聊流的便捷函数"""
    return ChatManager.get_private_stream_by_user_id(user_id, platform)


def get_stream_type(chat_stream: ChatStream):
    """获取聊天流类型的便捷函数"""
    return ChatManager.get_stream_type(chat_stream)


def get_stream_info(chat_stream: ChatStream):
    """获取聊天流信息的便捷函数"""
    return ChatManager.get_stream_info(chat_stream)


def get_streams_summary():
    """获取聊天流统计摘要的便捷函数"""
    return ChatManager.get_streams_summary()
