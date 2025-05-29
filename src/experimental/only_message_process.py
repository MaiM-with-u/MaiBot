from src.common.logger_manager import get_logger
from src.chat.message_receive.message import MessageRecv
from src.chat.message_receive.storage import MessageStorage
from src.config.config import global_config
from src.chat.message_receive.chat_stream import ChatStream

from maim_message import UserInfo
from datetime import datetime
import re

logger = get_logger("pfc")


class MessageProcessor:
    """消息处理器，负责处理接收到的消息并存储"""

    def __init__(self):
        self.storage = MessageStorage()

    @staticmethod
    def _check_ban_words(text: str, chat: ChatStream, userinfo: UserInfo) -> bool:
        """检查消息中是否包含过滤词"""
        for word in global_config.message_receive.ban_words:
            if word in text:
                logger.info(
                    f"[{chat.group_info.group_name if chat.group_info else '私聊'}]{userinfo.user_nickname}:{text}"
                )
                logger.info(f"[过滤词识别]消息中含有{word}，filtered")
                return True
        return False

    @staticmethod
    def _check_ban_regex(text: str, chat: ChatStream, userinfo: UserInfo) -> bool:
        """检查消息是否匹配过滤正则表达式"""
        for pattern in global_config.message_receive.ban_msgs_regex:
            if re.search(pattern, text):
                chat_name = chat.group_info.group_name if chat.group_info else "私聊"
                logger.info(f"[{chat_name}]{userinfo.user_nickname}:{text}")
                logger.info(f"[正则表达式过滤]消息匹配到{pattern}，filtered")
                return True
        return False

    async def process_message(self, message: MessageRecv) -> None:
        """处理消息并存储

        Args:
            message: 消息对象
        """
        userinfo = message.message_info.user_info
        chat = message.chat_stream

        # 处理消息
        await message.process()

        # 过滤词/正则表达式过滤
        if self._check_ban_words(message.processed_plain_text, chat, userinfo) or self._check_ban_regex(
            message.raw_message, chat, userinfo
        ):
            return

        # 存储消息
        await self.storage.store_message(message, chat)

        # 打印消息信息
        mes_name = chat.group_info.group_name if chat.group_info else "私聊"
        # 将时间戳转换为datetime对象
        current_time = datetime.fromtimestamp(message.message_info.time).strftime("%H:%M:%S")
        logger.info(
            f"[{current_time}][{mes_name}]{message.message_info.user_info.user_nickname}: {message.processed_plain_text}"
        )
