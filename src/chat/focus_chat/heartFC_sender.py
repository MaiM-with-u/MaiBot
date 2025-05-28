import asyncio
from typing import Dict, Optional  # 重新导入类型
from src.chat.message_receive.message import MessageSending, MessageThinking
from src.common.message.api import global_api
from src.chat.message_receive.storage import MessageStorage
from src.chat.utils.utils import truncate_message
from src.common.logger_manager import get_logger
from src.chat.utils.utils import calculate_typing_time
from rich.traceback import install
import traceback

install(extra_lines=3)


logger = get_logger("sender")


async def send_message(message: MessageSending) -> str:
    """合并后的消息发送函数，包含WS发送和日志记录"""
    message_preview = truncate_message(message.processed_plain_text, max_length=40)

    try:
        # 直接调用API发送消息
        await global_api.send_message(message)
        logger.success(f"已将消息  '{message_preview}'  发往平台'{message.message_info.platform}'")
        return message.processed_plain_text

    except Exception as e:
        logger.error(f"发送消息   '{message_preview}'   发往平台'{message.message_info.platform}' 失败: {str(e)}")
        traceback.print_exc()
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

    async def get_thinking_start_time(self, chat_id: str, message_id: str) -> Optional[float]:
        """获取已注册思考消息的开始时间。"""
        async with self._thinking_lock:
            thinking_message = self.thinking_messages.get(chat_id, {}).get(message_id)
            return thinking_message.thinking_start_time if thinking_message else None

    async def send_message(self, message: MessageSending, has_thinking=False, typing=False, set_reply=False):
        """
        处理、发送并存储一条消息。

        参数：
            message: MessageSending 对象，待发送的消息。
            has_thinking: 是否管理思考状态，表情包无思考状态（如需调用 register_thinking/complete_thinking）。
            typing: 是否模拟打字等待（根据 has_thinking 控制等待时长）。

        用法：
            - has_thinking=True 时，自动处理思考消息的时间和清理。
            - typing=True 时，发送前会有打字等待。
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
            if set_reply:
                _ = message.update_thinking_time()

                # --- 条件应用 set_reply 逻辑 ---
                if (
                    message.is_head
                    and not message.is_private_message()
                    and message.reply.processed_plain_text != "[System Trigger Context]"
                ):
                    message.set_reply(message.reply)
                    logger.debug(f"[{chat_id}] 应用 set_reply 逻辑: {message.processed_plain_text[:20]}...")

            await message.process()

            if typing:
                if has_thinking:
                    typing_time = calculate_typing_time(
                        input_string=message.processed_plain_text,
                        thinking_start_time=message.thinking_start_time,
                        is_emoji=message.is_emoji,
                    )
                    await asyncio.sleep(typing_time)
                else:
                    await asyncio.sleep(0.5)

            sent_msg = await send_message(message)
            await self.storage.store_message(message, message.chat_stream)

            if sent_msg:
                return sent_msg
            else:
                return "发送失败"

        except Exception as e:
            logger.error(f"[{chat_id}] 处理或存储消息 {message_id} 时出错: {e}")
            raise e
        finally:
            await self.complete_thinking(chat_id, message_id)
