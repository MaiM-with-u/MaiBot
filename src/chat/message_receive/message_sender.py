# src/plugins/chat/message_sender.py
import asyncio
import time
from asyncio import Task
from typing import Union
from src.common.message.api import global_api

# from ...common.database import db # 数据库依赖似乎不需要了，注释掉
from .message import MessageSending, MessageThinking, MessageSet

from .storage import MessageStorage
from ...config.config import global_config
from ..utils.utils import truncate_message, calculate_typing_time, count_messages_between

from src.common.logger_manager import get_logger
from rich.traceback import install

install(extra_lines=3)


logger = get_logger("sender")


async def send_via_ws(message: MessageSending) -> None:
    """通过 WebSocket 发送消息"""
    try:
        await global_api.send_message(message)
    except Exception as e:
        logger.error(f"WS发送失败: {e}")
        raise ValueError(f"未找到平台：{message.message_info.platform} 的url配置，请检查配置文件") from e


async def send_message(
    message: MessageSending,
) -> None:
    """发送消息（核心发送逻辑）"""

    # --- 添加计算打字和延迟的逻辑 (从 heartflow_message_sender 移动并调整) ---
    typing_time = calculate_typing_time(
        input_string=message.processed_plain_text,
        thinking_start_time=message.thinking_start_time,
        is_emoji=message.is_emoji,
    )
    # logger.trace(f"{message.processed_plain_text},{typing_time},计算输入时间结束") # 减少日志
    await asyncio.sleep(typing_time)
    # logger.trace(f"{message.processed_plain_text},{typing_time},等待输入时间结束") # 减少日志
    # --- 结束打字延迟 ---

    message_preview = truncate_message(message.processed_plain_text)

    try:
        await send_via_ws(message)
        logger.success(f"发送消息   '{message_preview}'   成功")  # 调整日志格式
    except Exception as e:
        logger.error(f"发送消息   '{message_preview}'   失败: {str(e)}")


class MessageSender:
    """发送器 (不再是单例)"""

    def __init__(self):
        self.message_interval = (0.5, 1)  # 消息间隔时间范围(秒)
        self.last_send_time = 0
        self._current_bot = None

    def set_bot(self, bot):
        """设置当前bot实例"""
        pass


class MessageContainer:
    """单个聊天流的发送/思考消息容器"""

    def __init__(self, chat_id: str, max_size: int = 100):
        self.chat_id = chat_id
        self.max_size = max_size
        self.messages: list[MessageThinking | MessageSending] = []  # 明确类型
        self.last_send_time = 0
        self.thinking_wait_timeout = 20  # 思考等待超时时间（秒） - 从旧 sender 合并

    def count_thinking_messages(self) -> int:
        """计算当前容器中思考消息的数量"""
        return sum(1 for msg in self.messages if isinstance(msg, MessageThinking))

    def get_timeout_sending_messages(self) -> list[MessageSending]:
        """获取所有超时的MessageSending对象（思考时间超过20秒），按thinking_start_time排序 - 从旧 sender 合并"""
        current_time = time.time()
        timeout_messages = []

        for msg in self.messages:
            # 只检查 MessageSending 类型
            if isinstance(msg, MessageSending):
                # 确保 thinking_start_time 有效
                if msg.thinking_start_time and current_time - msg.thinking_start_time > self.thinking_wait_timeout:
                    timeout_messages.append(msg)

        # 按thinking_start_time排序，时间早的在前面
        timeout_messages.sort(key=lambda x: x.thinking_start_time)
        return timeout_messages

    def get_earliest_message(self):
        """获取thinking_start_time最早的消息对象"""
        if not self.messages:
            return None
        earliest_time = float("inf")
        earliest_message = None
        for msg in self.messages:
            # 确保消息有 thinking_start_time 属性
            msg_time = getattr(msg, "thinking_start_time", float("inf"))
            if msg_time < earliest_time:
                earliest_time = msg_time
                earliest_message = msg
        return earliest_message

    def add_message(self, message: Union[MessageThinking, MessageSending, MessageSet]):
        """添加消息到队列"""
        if isinstance(message, MessageSet):
            for single_message in message.messages:
                self.messages.append(single_message)
        else:
            self.messages.append(message)

    def remove_message(self, message_to_remove: Union[MessageThinking, MessageSending]):
        """移除指定的消息对象，如果消息存在则返回True，否则返回False"""
        try:
            _initial_len = len(self.messages)
            # 使用列表推导式或 message_filter 创建新列表，排除要删除的元素
            # self.messages = [msg for msg in self.messages if msg is not message_to_remove]
            # 或者直接 remove (如果确定对象唯一性)
            if message_to_remove in self.messages:
                self.messages.remove(message_to_remove)
                return True
            # logger.debug(f"Removed message {getattr(message_to_remove, 'message_info', {}).get('message_id', 'UNKNOWN')}. Old len: {initial_len}, New len: {len(self.messages)}")
            # return len(self.messages) < initial_len
            return False

        except Exception as e:
            logger.exception(f"移除消息时发生错误: {e}")
            return False

    def has_messages(self) -> bool:
        """检查是否有待发送的消息"""
        return bool(self.messages)

    def get_all_messages(self) -> list[MessageThinking | MessageSending]:
        """获取所有消息"""
        return list(self.messages)  # 返回副本


class MessageManager:
    """管理所有聊天流的消息容器 (不再是单例)"""

    def __init__(self):
        self._processor_task: Task | None = None
        self.containers: dict[str, MessageContainer] = {}
        self.storage = MessageStorage()  # 添加 storage 实例
        self._running = True  # 处理器运行状态
        self._container_lock = asyncio.Lock()  # 保护 containers 字典的锁
        # self.message_sender = MessageSender() # 创建发送器实例 (改为全局实例)

    async def start(self):
        """启动后台处理器任务。"""
        # 检查是否已有任务在运行，避免重复启动
        if self._processor_task is not None and not self._processor_task.done():
            logger.warning("Processor task already running.")
            return
        self._processor_task = asyncio.create_task(self._start_processor_loop())
        logger.debug("MessageManager processor task started.")

    def stop(self):
        """停止后台处理器任务。"""
        self._running = False
        if self._processor_task is not None and not self._processor_task.done():
            self._processor_task.cancel()
            logger.debug("MessageManager processor task stopping.")
        else:
            logger.debug("MessageManager processor task not running or already stopped.")

    async def get_container(self, chat_id: str) -> MessageContainer:
        """获取或创建聊天流的消息容器 (异步，使用锁)"""
        async with self._container_lock:
            if chat_id not in self.containers:
                self.containers[chat_id] = MessageContainer(chat_id)
            return self.containers[chat_id]

    async def add_message(self, message: Union[MessageThinking, MessageSending, MessageSet]) -> None:
        """添加消息到对应容器"""
        chat_stream = message.chat_stream
        if not chat_stream:
            logger.error("消息缺少 chat_stream，无法添加到容器")
            return  # 或者抛出异常
        container = await self.get_container(chat_stream.stream_id)
        container.add_message(message)

    def check_if_sending_message_exist(self, chat_id, thinking_id):
        """检查指定聊天流的容器中是否存在具有特定 thinking_id 的 MessageSending 消息 或 emoji 消息"""
        # 这个方法现在是非异步的，因为它只读取数据
        container = self.containers.get(chat_id)  # 直接 get，因为读取不需要锁
        if container and container.has_messages():
            for message in container.get_all_messages():
                if isinstance(message, MessageSending):
                    msg_id = getattr(message.message_info, "message_id", None)
                    # 检查 message_id 是否匹配 thinking_id 或以 "me" 开头 (emoji)
                    if msg_id == thinking_id or (msg_id and msg_id.startswith("me")):
                        # logger.debug(f"检查到存在相同thinking_id或emoji的消息: {msg_id} for {thinking_id}")
                        return True
        return False

    async def _handle_sending_message(self, container: MessageContainer, message: MessageSending):
        """处理单个 MessageSending 消息 (包含 set_reply 逻辑)"""
        try:
            _ = message.update_thinking_time()  # 更新思考时间
            thinking_start_time = message.thinking_start_time
            now_time = time.time()
            # logger.debug(f"thinking_start_time:{thinking_start_time},now_time:{now_time}")
            thinking_messages_count, thinking_messages_length = count_messages_between(
                start_time=thinking_start_time, end_time=now_time, stream_id=message.chat_stream.stream_id
            )
            # print(f"message.reply:{message.reply}")

            # --- 条件应用 set_reply 逻辑 ---
            # logger.debug(
            #     f"[message.apply_set_reply_logic:{message.apply_set_reply_logic},message.is_head:{message.is_head},thinking_messages_count:{thinking_messages_count},thinking_messages_length:{thinking_messages_length},message.is_private_message():{message.is_private_message()}]"
            # )
            if (
                # message.apply_set_reply_logic  # 检查标记
                # and message.is_head
                message.is_head
                and (thinking_messages_count > 3 or thinking_messages_length > 200)
                and not message.is_private_message()
            ):
                logger.debug(
                    f"[{message.chat_stream.stream_id}] 应用 set_reply 逻辑: {message.processed_plain_text[:20]}..."
                )
                message.set_reply(message.reply)
            # --- 结束条件 set_reply ---

            await message.process()  # 预处理消息内容

            # logger.debug(f"{message}")

            # 使用全局 message_sender 实例
            await send_message(message)
            await self.storage.store_message(message, message.chat_stream)

            # 移除消息要在发送 *之后*
            container.remove_message(message)
            # logger.debug(f"[{message.chat_stream.stream_id}] Sent and removed message: {message.message_info.message_id}")

        except Exception as e:
            logger.error(
                f"[{message.chat_stream.stream_id}] 处理发送消息 {getattr(message.message_info, 'message_id', 'N/A')} 时出错: {e}"
            )
            logger.exception("详细错误信息:")
            # 考虑是否移除出错的消息，防止无限循环
            removed = container.remove_message(message)
            if removed:
                logger.warning(f"[{message.chat_stream.stream_id}] 已移除处理出错的消息。")

    async def _process_chat_messages(self, chat_id: str):
        """处理单个聊天流消息 (合并后的逻辑)"""
        container = await self.get_container(chat_id)  # 获取容器是异步的了

        if container.has_messages():
            message_earliest = container.get_earliest_message()

            if not message_earliest:  # 如果最早消息为空，则退出
                return

            if isinstance(message_earliest, MessageThinking):
                # --- 处理思考消息 (来自旧 sender) ---
                message_earliest.update_thinking_time()
                thinking_time = message_earliest.thinking_time
                # 减少控制台刷新频率或只在时间显著变化时打印
                if int(thinking_time) % 5 == 0:  # 每5秒打印一次
                    print(
                        f"消息 {message_earliest.message_info.message_id} 正在思考中，已思考 {int(thinking_time)} 秒\r",
                        end="",
                        flush=True,
                    )

                # 检查是否超时
                if thinking_time > global_config.normal_chat.thinking_timeout:
                    logger.warning(
                        f"[{chat_id}] 消息思考超时 ({thinking_time:.1f}秒)，移除消息 {message_earliest.message_info.message_id}"
                    )
                    container.remove_message(message_earliest)
                    print()  # 超时后换行，避免覆盖下一条日志

            elif isinstance(message_earliest, MessageSending):
                # --- 处理发送消息 ---
                await self._handle_sending_message(container, message_earliest)

            # --- 处理超时发送消息 (来自旧 sender) ---
            # 在处理完最早的消息后，检查是否有超时的发送消息
            timeout_sending_messages = container.get_timeout_sending_messages()
            if timeout_sending_messages:
                logger.debug(f"[{chat_id}] 发现 {len(timeout_sending_messages)} 条超时的发送消息")
                for msg in timeout_sending_messages:
                    # 确保不是刚刚处理过的最早消息 (虽然理论上应该已被移除，但以防万一)
                    if msg is message_earliest:
                        continue
                    logger.info(f"[{chat_id}] 处理超时发送消息: {msg.message_info.message_id}")
                    await self._handle_sending_message(container, msg)  # 复用处理逻辑

        # 清理空容器 (可选)
        # async with self._container_lock:
        #     if not container.has_messages() and chat_id in self.containers:
        #         logger.debug(f"[{chat_id}] 容器已空，准备移除。")
        #         del self.containers[chat_id]

    async def _start_processor_loop(self):
        """消息处理器主循环"""
        while self._running:
            tasks = []
            # 使用异步锁保护迭代器创建过程
            async with self._container_lock:
                # 创建 keys 的快照以安全迭代
                chat_ids = list(self.containers.keys())

            for chat_id in chat_ids:
                # 为每个 chat_id 创建一个处理任务
                tasks.append(asyncio.create_task(self._process_chat_messages(chat_id)))

            if tasks:
                try:
                    # 等待当前批次的所有任务完成
                    await asyncio.gather(*tasks)
                except Exception as e:
                    logger.error(f"消息处理循环 gather 出错: {e}")

            # 等待一小段时间，避免CPU空转
            try:
                await asyncio.sleep(0.1)  # 稍微降低轮询频率
            except asyncio.CancelledError:
                logger.info("Processor loop sleep cancelled.")
                break  # 退出循环
        logger.info("MessageManager processor loop finished.")


# --- 创建全局实例 ---
message_manager = MessageManager()
message_sender = MessageSender()
# --- 结束全局实例 ---
