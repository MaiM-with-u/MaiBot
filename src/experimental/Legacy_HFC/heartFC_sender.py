# src/plugins/heartFC_chat/heartFC_sender.py
import asyncio  # 重新导入 asyncio
from typing import Dict, Optional  # 重新导入类型
from src.chat.message_receive.message import MessageSending, MessageThinking  # 只保留 MessageSending 和 MessageThinking

# from ..message import global_api
from src.common.message import global_api
from src.chat.message_receive.storage import MessageStorage
from src.chat.utils.utils import truncate_message
from src.common.logger_manager import get_logger
from src.chat.utils.utils import calculate_typing_time
from rich.traceback import install

install(extra_lines=3)


logger = get_logger("sender")


async def send_message(message: MessageSending) -> None:
    """合并后的消息发送函数，包含WS发送和日志记录"""
    message_preview = truncate_message(message.processed_plain_text)

    try:
        # 直接调用API发送消息
        await global_api.send_message(message)
        logger.success(f"发送消息   '{message_preview}'   成功")

    except Exception as e:
        logger.error(f"发送消息   '{message_preview}'   失败: {str(e)}")
        if not message.message_info.platform:
            raise ValueError(f"未找到平台：{message.message_info.platform} 的url配置，请检查配置文件") from e
        raise e  # 重新抛出其他异常


class HeartFCSender:
    """管理消息的注册、即时处理、发送和存储，并跟踪思考状态。"""

    def __init__(self):
        self.storage = MessageStorage()
        # 用于存储活跃的思考消息
        self.thinking_messages: Dict[str, Dict[str, MessageThinking]] = {}
        self._thinking_lock = asyncio.Lock()  # 保护 thinking_messages 的锁

    async def register_thinking(self, thinking_message: MessageThinking):
        """注册一个思考中的消息。"""
        if not thinking_message.chat_stream or not thinking_message.message_info.message_id:
            logger.error("无法注册缺少 chat_stream 或 message_id 的思考消息")
            return

        chat_id = thinking_message.chat_stream.stream_id
        message_id = thinking_message.message_info.message_id

        async with self._thinking_lock:
            if chat_id not in self.thinking_messages:
                self.thinking_messages[chat_id] = {}
            if message_id in self.thinking_messages[chat_id]:
                logger.warning(f"[{chat_id}] 尝试注册已存在的思考消息 ID: {message_id}")
            self.thinking_messages[chat_id][message_id] = thinking_message
            logger.debug(f"[{chat_id}] Registered thinking message: {message_id}")

    async def complete_thinking(self, chat_id: str, message_id: str):
        """完成并移除一个思考中的消息记录。"""
        async with self._thinking_lock:
            if chat_id in self.thinking_messages and message_id in self.thinking_messages[chat_id]:
                del self.thinking_messages[chat_id][message_id]
                logger.debug(f"[{chat_id}] Completed thinking message: {message_id}")
                if not self.thinking_messages[chat_id]:
                    del self.thinking_messages[chat_id]
                    logger.debug(f"[{chat_id}] Removed empty thinking message container.")

    def is_thinking(self, chat_id: str, message_id: str) -> bool:
        """检查指定的消息 ID 是否当前正处于思考状态。"""
        return chat_id in self.thinking_messages and message_id in self.thinking_messages[chat_id]

    async def get_thinking_start_time(self, chat_id: str, message_id: str) -> Optional[float]:
        """获取已注册思考消息的开始时间。"""
        async with self._thinking_lock:
            thinking_message = self.thinking_messages.get(chat_id, {}).get(message_id)
            return thinking_message.thinking_start_time if thinking_message else None

    async def type_and_send_message(self, message: MessageSending, typing=False):
        """
        立即处理、发送并存储单个 MessageSending 消息。
        调用此方法前，应先调用 register_thinking 注册对应的思考消息。
        此方法执行后会调用 complete_thinking 清理思考状态。
        """
        if not message.chat_stream:
            logger.error("消息缺少 chat_stream，无法发送")
            return
        if not message.message_info or not message.message_info.message_id:
            logger.error("消息缺少 message_info 或 message_id，无法发送")
            return

        chat_id = message.chat_stream.stream_id
        message_id = message.message_info.message_id

        try:
            _ = message.update_thinking_time()

            # --- 条件应用 set_reply 逻辑 ---
            if message.apply_set_reply_logic and message.is_head and not message.is_private_message():
                logger.debug(f"[{chat_id}] 应用 set_reply 逻辑: {message.processed_plain_text[:20]}...")
                message.set_reply()
            # --- 结束条件 set_reply ---

            await message.process()

            if typing:
                typing_time = calculate_typing_time(
                    input_string=message.processed_plain_text,
                    thinking_start_time=message.thinking_start_time,
                    is_emoji=message.is_emoji,
                )
                await asyncio.sleep(typing_time)

            await send_message(message)
            await self.storage.store_message(message, message.chat_stream)

        except Exception as e:
            logger.error(f"[{chat_id}] 处理或存储消息 {message_id} 时出错: {e}")
            raise e
        finally:
            await self.complete_thinking(chat_id, message_id)

    async def send_and_store(self, message: MessageSending):
        """处理、发送并存储单个消息，不涉及思考状态管理。"""
        if not message.chat_stream:
            logger.error(f"[{message.message_info.platform or 'UnknownPlatform'}] 消息缺少 chat_stream，无法发送")
            return
        if not message.message_info or not message.message_info.message_id:
            logger.error(
                f"[{message.chat_stream.stream_id if message.chat_stream else 'UnknownStream'}] 消息缺少 message_info 或 message_id，无法发送"
            )
            return

        chat_id = message.chat_stream.stream_id
        message_id = message.message_info.message_id  # 获取消息ID用于日志

        try:
            await message.process()

            await asyncio.sleep(0.5)

            await send_message(message)  # 使用现有的发送方法
            await self.storage.store_message(message, message.chat_stream)  # 使用现有的存储方法

        except Exception as e:
            logger.error(f"[{chat_id}] 处理或存储消息 {message_id} 时出错: {e}")
            # 重新抛出异常，让调用者知道失败了
            raise e
