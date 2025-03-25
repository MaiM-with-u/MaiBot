import asyncio
import time
from typing import Dict, List, Optional, Union

from src.common.logger import get_module_logger
from nonebot.adapters.onebot.v11 import Bot
from ...common.database import db
from .message_cq import MessageSendCQ
from .message import MessageSending, MessageThinking, MessageSet

from .storage import MessageStorage
from .config import global_config
from .utils import truncate_message

from src.common.logger import LogConfig, SENDER_STYLE_CONFIG

# 定义日志配置
sender_config = LogConfig(
    # 使用消息发送专用样式
    console_format=SENDER_STYLE_CONFIG["console_format"],
    file_format=SENDER_STYLE_CONFIG["file_format"],
)

logger = get_module_logger("msg_sender", config=sender_config)


class Message_Sender:
    """发送器"""

    def __init__(self):
        self.message_interval = (0.5, 1)  # 消息间隔时间范围(秒)
        self.last_send_time = 0
        self._current_bot = None

    def set_bot(self, bot: Bot):
        """设置当前bot实例"""
        self._current_bot = bot

    def get_recalled_messages(self, stream_id: str) -> list:
        """获取所有撤回的消息"""
        recalled_messages = []

        recalled_messages = list(db.recalled_messages.find({"stream_id": stream_id}, {"message_id": 1}))
        # 按thinking_start_time排序，时间早的在前面
        return recalled_messages

    async def send_message(
        self,
        message: MessageSending,
    ) -> None:
        """发送消息"""

        if isinstance(message, MessageSending):
            recalled_messages = self.get_recalled_messages(message.chat_stream.stream_id)
            is_recalled = False
            for recalled_message in recalled_messages:
                if message.reply_to_message_id == recalled_message["message_id"]:
                    is_recalled = True
                    logger.warning(f"消息“{message.processed_plain_text}”已被撤回，不发送")
                    break
            if not is_recalled:
                message_json = message.to_dict()
                message_send = MessageSendCQ(data=message_json)
                message_preview = truncate_message(message.processed_plain_text)
                if message_send.message_info.group_info and message_send.message_info.group_info.group_id:
                    try:
                        await self._current_bot.send_group_msg(
                            group_id=message.message_info.group_info.group_id,
                            message=message_send.raw_message,
                            auto_escape=False,
                        )
                        logger.success(f"发送消息“{message_preview}”成功")
                    except Exception as e:
                        logger.error(f"[调试] 发生错误 {e}")
                        logger.error(f"[调试] 发送消息“{message_preview}”失败")
                else:
                    try:
                        logger.debug(message.message_info.user_info)
                        await self._current_bot.send_private_msg(
                            user_id=message.sender_info.user_id,
                            message=message_send.raw_message,
                            auto_escape=False,
                        )
                        logger.success(f"发送消息“{message_preview}”成功")
                    except Exception as e:
                        logger.error(f"[调试] 发生错误 {e}")
                        logger.error(f"[调试] 发送消息“{message_preview}”失败")


class MessageContainer:
    """单个聊天流的发送/思考消息容器"""

    def __init__(self, chat_id: str, max_size: int = 100):
        self.chat_id = chat_id
        self.max_size = max_size
        self.messages = []
        self.last_send_time = 0
        self.thinking_timeout = 20  # 思考超时时间（秒）

    def get_timeout_messages(self) -> List[MessageSending]:
        """获取所有超时的Message_Sending对象（思考时间超过30秒），按thinking_start_time排序"""
        current_time = time.time()
        timeout_messages = []

        for msg in self.messages:
            if isinstance(msg, MessageSending):
                if current_time - msg.thinking_start_time > self.thinking_timeout:
                    timeout_messages.append(msg)

        # 按thinking_start_time排序，时间早的在前面
        timeout_messages.sort(key=lambda x: x.thinking_start_time)

        return timeout_messages

    def get_earliest_message(self) -> Optional[Union[MessageThinking, MessageSending]]:
        """获取thinking_start_time最早的消息对象"""
        if not self.messages:
            return None
        earliest_time = float("inf")
        earliest_message = None
        for msg in self.messages:
            msg_time = msg.thinking_start_time
            if msg_time < earliest_time:
                earliest_time = msg_time
                earliest_message = msg
        return earliest_message

    def add_message(self, message: Union[MessageThinking, MessageSending]) -> None:
        """添加消息到队列"""
        if isinstance(message, MessageSet):
            for single_message in message.messages:
                self.messages.append(single_message)
        else:
            self.messages.append(message)

    def remove_message(self, message: Union[MessageThinking, MessageSending]) -> bool:
        """移除消息，如果消息存在则返回True，否则返回False"""
        try:
            if message in self.messages:
                self.messages.remove(message)
                return True
            return False
        except Exception:
            logger.exception("移除消息时发生错误")
            return False

    def has_messages(self) -> bool:
        """检查是否有待发送的消息"""
        return bool(self.messages)

    def get_all_messages(self) -> List[Union[MessageSending, MessageThinking]]:
        """获取所有消息"""
        return list(self.messages)


class MessageManager:
    """管理所有聊天流的消息容器"""

    def __init__(self):
        self.containers: Dict[str, MessageContainer] = {}  # chat_id -> MessageContainer
        self.storage = MessageStorage()
        self._running = True

    def get_container(self, chat_id: str) -> MessageContainer:
        """获取或创建聊天流的消息容器"""
        if chat_id not in self.containers:
            self.containers[chat_id] = MessageContainer(chat_id)
        return self.containers[chat_id]

    def add_message(self, message: Union[MessageThinking, MessageSending, MessageSet]) -> None:
        chat_stream = message.chat_stream
        if not chat_stream:
            raise ValueError("无法找到对应的聊天流")
        container = self.get_container(chat_stream.stream_id)
        container.add_message(message)

    async def process_chat_messages(self, chat_id: str):
        """处理聊天流消息"""
        container = self.get_container(chat_id)
        if container.has_messages():
            # print(f"处理有message的容器chat_id: {chat_id}")
            message_earliest = container.get_earliest_message()

            if isinstance(message_earliest, MessageThinking):
                message_earliest.update_thinking_time()
                thinking_time = message_earliest.thinking_time
                # print(thinking_time)
                print(
                    f"消息正在思考中，已思考{int(thinking_time)}秒\r",
                    end="",
                    flush=True,
                )

                # 检查是否超时
                if thinking_time > global_config.thinking_timeout:
                    logger.warning(f"消息思考超时({thinking_time}秒)，移除该消息")
                    container.remove_message(message_earliest)

            else:
                # print(message_earliest.is_head)
                # print(message_earliest.update_thinking_time())
                # print(message_earliest.is_private_message())
                # thinking_time = message_earliest.update_thinking_time()
                # print(thinking_time)
                if (
                    message_earliest.is_head
                    and message_earliest.update_thinking_time() > 15
                    and not message_earliest.is_private_message()  # 避免在私聊时插入reply
                ):
                    logger.debug(f"设置回复消息{message_earliest.processed_plain_text}")
                    message_earliest.set_reply()

                await message_earliest.process()

                await message_sender.send_message(message_earliest)

                await self.storage.store_message(message_earliest, message_earliest.chat_stream, None)

                container.remove_message(message_earliest)

            message_timeout = container.get_timeout_messages()
            if message_timeout:
                logger.debug(f"发现{len(message_timeout)}条超时消息")
                for msg in message_timeout:
                    if msg == message_earliest:
                        continue

                    try:
                        # print(msg.is_head)
                        # print(msg.update_thinking_time())
                        # print(msg.is_private_message())
                        if (
                            msg.is_head
                            and msg.update_thinking_time() > 15
                            and not msg.is_private_message()  # 避免在私聊时插入reply
                        ):
                            logger.debug(f"设置回复消息{msg.processed_plain_text}")
                            msg.set_reply()

                        await msg.process()

                        await message_sender.send_message(msg)

                        await self.storage.store_message(msg, msg.chat_stream, None)

                        if not container.remove_message(msg):
                            logger.warning("尝试删除不存在的消息")
                    except Exception:
                        logger.exception("处理超时消息时发生错误")
                        continue

    async def start_processor(self):
        """启动消息处理器"""
        while self._running:
            await asyncio.sleep(1)
            tasks = []
            for chat_id in self.containers.keys():
                tasks.append(self.process_chat_messages(chat_id))

            await asyncio.gather(*tasks)


# 创建全局消息管理器实例
message_manager = MessageManager()
# 创建全局发送器实例
message_sender = Message_Sender()
