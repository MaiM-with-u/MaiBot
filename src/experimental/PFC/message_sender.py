import time
from typing import Optional
from src.common.logger_manager import get_logger
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.message_receive.message import Message
from maim_message import UserInfo, Seg
from src.chat.message_receive.message import MessageSending, MessageSet
from src.chat.message_receive.message_sender import message_manager
from src.config.config import global_config
from rich.traceback import install

install(extra_lines=3)


logger = get_logger("pfc_sender")


class DirectMessageSender:
    """直接消息发送器"""

    def __init__(self, private_name: str):
        self.private_name = private_name

    async def send_message(
        self,
        chat_stream: ChatStream,
        segments: Seg,
        reply_to_message: Optional[Message] = None,
        is_emoji: Optional[bool] = False,
        content: str = None,
    ) -> None:
        """发送消息到聊天流

        Args:
            chat_stream: 聊天流
            content: 消息内容
            reply_to_message: 要回复的消息（可选）
        """
        try:
            # 获取麦麦的信息
            bot_user_info = UserInfo(
                user_id=global_config.BOT_QQ,
                user_nickname=global_config.BOT_NICKNAME,
                platform=chat_stream.platform,
            )

            # 用当前时间作为message_id，和之前那套sender一样
            message_id = f"dm{round(time.time(), 2)}"

            # 构建消息对象
            message = MessageSending(
                message_id=message_id,
                chat_stream=chat_stream,
                bot_user_info=bot_user_info,
                sender_info=reply_to_message.message_info.user_info if reply_to_message else None,
                message_segment=segments,
                reply=reply_to_message,
                is_head=True,
                is_emoji=is_emoji,
                thinking_start_time=time.time(),
            )

            # 处理消息
            await message.process()

            # 不知道有什么用，先留下来了，和之前那套sender一样
            _message_json = message.to_dict()

            # 发送消息
            message_set = MessageSet(chat_stream, message_id)
            message_set.add_message(message)
            await message_manager.add_message(message_set)
            if is_emoji:
                logger.info(f"[私聊][{self.private_name}]PFC表情消息已发送: {content}")
            else:
                logger.info(f"[私聊][{self.private_name}]PFC消息已发送: {content}")

        except Exception as e:
            logger.error(f"[私聊][{self.private_name}]PFC消息发送失败: {str(e)}")
            raise
