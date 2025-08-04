import asyncio
import traceback
import time
import random
from typing import Optional, Dict, Tuple, List  # 导入类型提示
from maim_message import UserInfo, Seg
from src.common.logger import get_logger
from src.chat.message_receive.chat_stream import ChatStream, get_chat_manager
from .s4u_stream_generator import S4UStreamGenerator
from src.chat.message_receive.message import MessageSending, MessageRecv, MessageRecvS4U
from src.config.config import global_config
from src.common.message.api import get_global_api
from src.chat.message_receive.storage import MessageStorage
from .s4u_watching_manager import watching_manager
import json
from .s4u_mood_manager import mood_manager
from src.person_info.relationship_builder_manager import relationship_builder_manager
from src.mais4u.s4u_config import s4u_config
from src.person_info.person_info import PersonInfoManager
from .super_chat_manager import get_super_chat_manager
from .yes_or_no import yes_or_no_head
from src.mais4u.constant_s4u import ENABLE_S4U

logger = get_logger("S4U_chat")


class MessageSenderContainer:
    """一个简单的容器，用于按顺序发送消息并模拟打字效果。"""

    def __init__(self, chat_stream: ChatStream, original_message: MessageRecv):
        self.chat_stream = chat_stream
        self.original_message = original_message
        self.queue = asyncio.Queue()
        self.storage = MessageStorage()
        self._task: Optional[asyncio.Task] = None
        self._paused_event = asyncio.Event()
        self._paused_event.set()  # 默认设置为非暂停状态
        
        self.msg_id = ""
        
        self.last_msg_id = ""
        
        self.voice_done = ""
        

        

    async def add_message(self, chunk: str):
        """向队列中添加一个消息块。"""
        await self.queue.put(chunk)

    async def close(self):
        """表示没有更多消息了，关闭队列。"""
        await self.queue.put(None)  # Sentinel

    def pause(self):
        """暂停发送。"""
        self._paused_event.clear()

    def resume(self):
        """恢复发送。"""
        self._paused_event.set()

    def _calculate_typing_delay(self, text: str) -> float:
        """根据文本长度计算模拟打字延迟。"""
        chars_per_second = s4u_config.chars_per_second
        min_delay = s4u_config.min_typing_delay
        max_delay = s4u_config.max_typing_delay

        delay = len(text) / chars_per_second
        return max(min_delay, min(delay, max_delay))

    async def _send_worker(self):
        """从队列中取出消息并发送。"""
        while True:
            try:
                # This structure ensures that task_done() is called for every item retrieved,
                # even if the worker is cancelled while processing the item.
                chunk = await self.queue.get()
            except asyncio.CancelledError:
                break

            try:
                if chunk is None:
                    break

                # Check for pause signal *after* getting an item.
                await self._paused_event.wait()

                # 根据配置选择延迟模式
                if s4u_config.enable_dynamic_typing_delay:
                    delay = self._calculate_typing_delay(chunk)
                else:
                    delay = s4u_config.typing_delay
                await asyncio.sleep(delay)

                message_segment = Seg(type="tts_text", data=f"{self.msg_id}:{chunk}")
                bot_message = MessageSending(
                    message_id=self.msg_id,
                    chat_stream=self.chat_stream,
                    bot_user_info=UserInfo(
                        user_id=global_config.bot.qq_account,
                        user_nickname=global_config.bot.nickname,
                        platform=self.original_message.message_info.platform,
                    ),
                    sender_info=self.original_message.message_info.user_info,
                    message_segment=message_segment,
                    reply=self.original_message,
                    is_emoji=False,
                    apply_set_reply_logic=True,
                    reply_to=f"{self.original_message.message_info.user_info.platform}:{self.original_message.message_info.user_info.user_id}",
                )

                await bot_message.process()

                await get_global_api().send_message(bot_message)
                logger.info(f"已将消息 '{self.msg_id}:{chunk}' 发往平台 '{bot_message.message_info.platform}'")

                message_segment = Seg(type="text", data=chunk)
                bot_message = MessageSending(
                    message_id=self.msg_id,
                    chat_stream=self.chat_stream,
                    bot_user_info=UserInfo(
                        user_id=global_config.bot.qq_account,
                        user_nickname=global_config.bot.nickname,
                        platform=self.original_message.message_info.platform,
                    ),
                    sender_info=self.original_message.message_info.user_info,
                    message_segment=message_segment,
                    reply=self.original_message,
                    is_emoji=False,
                    apply_set_reply_logic=True,
                    reply_to=f"{self.original_message.message_info.user_info.platform}:{self.original_message.message_info.user_info.user_id}",
                )
                await bot_message.process()
                
                await self.storage.store_message(bot_message, self.chat_stream)

            except Exception as e:
                logger.error(f"[消息流: {self.chat_stream.stream_id}] 消息发送或存储时出现错误: {e}", exc_info=True)

            finally:
                # CRUCIAL: Always call task_done() for any item that was successfully retrieved.
                self.queue.task_done()

    def start(self):
        """启动发送任务。"""
        if self._task is None:
            self._task = asyncio.create_task(self._send_worker())

    async def join(self):
        """等待所有消息发送完毕。"""
        if self._task:
            await self._task


class S4UChatManager:
    def __init__(self):
        self.s4u_chats: Dict[str, "S4UChat"] = {}

    def get_or_create_chat(self, chat_stream: ChatStream) -> "S4UChat":
        if chat_stream.stream_id not in self.s4u_chats:
            stream_name = get_chat_manager().get_stream_name(chat_stream.stream_id) or chat_stream.stream_id
            logger.info(f"Creating new S4UChat for stream: {stream_name}")
            self.s4u_chats[chat_stream.stream_id] = S4UChat(chat_stream)
        return self.s4u_chats[chat_stream.stream_id]


if not ENABLE_S4U:
    s4u_chat_manager = None
else:
    s4u_chat_manager = S4UChatManager()


def get_s4u_chat_manager() -> S4UChatManager:
    return s4u_chat_manager


class S4UChat:
    def __init__(self, chat_stream: ChatStream):
        """初始化 S4UChat 实例。"""

        self.chat_stream = chat_stream
        self.stream_id = chat_stream.stream_id
        self.stream_name = get_chat_manager().get_stream_name(self.stream_id) or self.stream_id
        self.relationship_builder = relationship_builder_manager.get_or_create_builder(self.stream_id)

        # 两个消息队列
        self._vip_queue = asyncio.PriorityQueue()
        self._normal_queue = asyncio.PriorityQueue()

        self._entry_counter = 0  # 保证FIFO的全局计数器
        self._new_message_event = asyncio.Event()  # 用于唤醒处理器

        self._processing_task = asyncio.create_task(self._message_processor())
        self._current_generation_task: Optional[asyncio.Task] = None
        # 当前消息的元数据：(队列类型, 优先级分数, 计数器, 消息对象)
        self._current_message_being_replied: Optional[Tuple[str, float, int, MessageRecv]] = None

        self._is_replying = False
        self.gpt = S4UStreamGenerator()
        self.gpt.chat_stream = self.chat_stream
        self.interest_dict: Dict[str, float] = {}  # 用户兴趣分
        
        self.internal_message :List[MessageRecvS4U] = []
        
        self.msg_id = ""
        self.voice_done = ""
        
        logger.info(f"[{self.stream_name}] S4UChat with two-queue system initialized.")

    def _get_priority_info(self, message: MessageRecv) -> dict:
        """安全地从消息中提取和解析 priority_info"""
        priority_info_raw = message.priority_info
        priority_info = {}
        if isinstance(priority_info_raw, str):
            try:
                priority_info = json.loads(priority_info_raw)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse priority_info JSON: {priority_info_raw}")
        elif isinstance(priority_info_raw, dict):
            priority_info = priority_info_raw
        return priority_info

    def _is_vip(self, priority_info: dict) -> bool:
        """检查消息是否来自VIP用户。"""
        return priority_info.get("message_type") == "vip"

    def _get_interest_score(self, user_id: str) -> float:
        """获取用户的兴趣分，默认为1.0"""
        return self.interest_dict.get(user_id, 1.0)
    
    def go_processing(self):
        if self.voice_done == self.last_msg_id:
            return True
        return False

    def _calculate_base_priority_score(self, message: MessageRecv, priority_info: dict) -> float:
        """
        为消息计算基础优先级分数。分数越高，优先级越高。
        """
        score = 0.0
        
        # 加上消息自带的优先级
        score += priority_info.get("message_priority", 0.0)

        # 加上用户的固有兴趣分
        score += self._get_interest_score(message.message_info.user_info.user_id)
        return score
    
    def decay_interest_score(self):
        for person_id, score in self.interest_dict.items():
            if score > 0:
                self.interest_dict[person_id] = score * 0.95
            else:
                self.interest_dict[person_id] = 0

    async def add_message(self, message: MessageRecvS4U|MessageRecv) -> None:
        
        self.decay_interest_score()
        
        """根据VIP状态和中断逻辑将消息放入相应队列。"""
        user_id = message.message_info.user_info.user_id
        platform = message.message_info.platform
        person_id = PersonInfoManager.get_person_id(platform, user_id)
        
        try:
            is_gift = message.is_gift
            is_superchat = message.is_superchat
            # print(is_gift)
            # print(is_superchat)
            if is_gift:
                await self.relationship_builder.build_relation(immediate_build=person_id)
                # 安全地增加兴趣分，如果person_id不存在则先初始化为1.0
                current_score = self.interest_dict.get(person_id, 1.0)
                self.interest_dict[person_id] = current_score + 0.1 * message.gift_count
            elif is_superchat:
                await self.relationship_builder.build_relation(immediate_build=person_id)
                # 安全地增加兴趣分，如果person_id不存在则先初始化为1.0
                current_score = self.interest_dict.get(person_id, 1.0)
                self.interest_dict[person_id] = current_score + 0.1 * float(message.superchat_price)
                
                # 添加SuperChat到管理器
                super_chat_manager = get_super_chat_manager()
                await super_chat_manager.add_superchat(message)
            else:
                await self.relationship_builder.build_relation(20)
        except Exception:
            traceback.print_exc()
            
        logger.info(f"[{self.stream_name}] 消息处理完毕，消息内容：{message.processed_plain_text}")
        
        priority_info = self._get_priority_info(message)
        is_vip = self._is_vip(priority_info)
        new_priority_score = self._calculate_base_priority_score(message, priority_info)

        should_interrupt = False
        if (s4u_config.enable_message_interruption and 
            self._current_generation_task and not self._current_generation_task.done()):
            if self._current_message_being_replied:
                current_queue, current_priority, _, current_msg = self._current_message_being_replied

                # 规则：VIP从不被打断
                if current_queue == "vip":
                    pass  # Do nothing

                # 规则：普通消息可以被打断
                elif current_queue == "normal":
                    # VIP消息可以打断普通消息
                    if is_vip:
                        should_interrupt = True
                        logger.info(f"[{self.stream_name}] VIP message received, interrupting current normal task.")
                    # 普通消息的内部打断逻辑
                    else:
                        new_sender_id = message.message_info.user_info.user_id
                        current_sender_id = current_msg.message_info.user_info.user_id
                        # 新消息优先级更高
                        if new_priority_score > current_priority:
                            should_interrupt = True
                            logger.info(f"[{self.stream_name}] New normal message has higher priority, interrupting.")
                        # 同用户，新消息的优先级不能更低
                        elif new_sender_id == current_sender_id and new_priority_score >= current_priority:
                            should_interrupt = True
                            logger.info(f"[{self.stream_name}] Same user sent new message, interrupting.")

        if should_interrupt:
            if self.gpt.partial_response:
                logger.warning(
                    f"[{self.stream_name}] Interrupting reply. Already generated: '{self.gpt.partial_response}'"
                )
            self._current_generation_task.cancel()

        # asyncio.PriorityQueue 是最小堆，所以我们存入分数的相反数
        # 这样，原始分数越高的消息，在队列中的优先级数字越小，越靠前
        item = (-new_priority_score, self._entry_counter, time.time(), message)

        if is_vip and s4u_config.vip_queue_priority:
            await self._vip_queue.put(item)
            logger.info(f"[{self.stream_name}] VIP message added to queue.")
        else:
            await self._normal_queue.put(item)

        self._entry_counter += 1
        self._new_message_event.set()  # 唤醒处理器

    def _cleanup_old_normal_messages(self):
        """清理普通队列中不在最近N条消息范围内的消息"""
        if not s4u_config.enable_old_message_cleanup or self._normal_queue.empty():
            return
        
        # 计算阈值：保留最近 recent_message_keep_count 条消息
        cutoff_counter = max(0, self._entry_counter - s4u_config.recent_message_keep_count)
        
        # 临时存储需要保留的消息
        temp_messages = []
        removed_count = 0
        
        # 取出所有普通队列中的消息
        while not self._normal_queue.empty():
            try:
                item = self._normal_queue.get_nowait()
                neg_priority, entry_count, timestamp, message = item
                
                # 如果消息在最近N条消息范围内，保留它
                logger.info(f"检查消息:{message.processed_plain_text},entry_count:{entry_count} cutoff_counter:{cutoff_counter}")
                
                if entry_count >= cutoff_counter:
                    temp_messages.append(item)
                else:
                    removed_count += 1
                    self._normal_queue.task_done()  # 标记被移除的任务为完成
                    
            except asyncio.QueueEmpty:
                break
        
        # 将保留的消息重新放入队列
        for item in temp_messages:
            self._normal_queue.put_nowait(item)
        
        if removed_count > 0:
            logger.info(f"消息{message.processed_plain_text}超过{s4u_config.recent_message_keep_count}条，现在counter:{self._entry_counter}被移除")
            logger.info(f"[{self.stream_name}] Cleaned up {removed_count} old normal messages outside recent {s4u_config.recent_message_keep_count} range.")

    async def _message_processor(self):
        """调度器：优先处理VIP队列，然后处理普通队列。"""
        while True:
            try:
                # 等待有新消息的信号，避免空转
                await self._new_message_event.wait()
                self._new_message_event.clear()
                
                # 清理普通队列中的过旧消息
                self._cleanup_old_normal_messages()

                # 优先处理VIP队列
                if not self._vip_queue.empty():
                    neg_priority, entry_count, _, message = self._vip_queue.get_nowait()
                    priority = -neg_priority
                    queue_name = "vip"
                # 其次处理普通队列
                elif not self._normal_queue.empty():

                    neg_priority, entry_count, timestamp, message = self._normal_queue.get_nowait()
                    priority = -neg_priority
                    # 检查普通消息是否超时
                    if time.time() - timestamp > s4u_config.message_timeout_seconds:
                        logger.info(
                            f"[{self.stream_name}] Discarding stale normal message: {message.processed_plain_text[:20]}..."
                        )
                        self._normal_queue.task_done()
                        continue  # 处理下一条
                    queue_name = "normal"
                else:
                    if self.internal_message:
                        message = self.internal_message[-1]
                        self.internal_message = []
                        
                        priority = 0
                        neg_priority = 0
                        entry_count = 0
                        queue_name = "internal"

                        logger.info(f"[{self.stream_name}] normal/vip 队列都空，触发 internal_message 回复: {getattr(message, 'processed_plain_text', str(message))[:20]}...")
                    else:
                        continue  # 没有消息了，回去等事件

                self._current_message_being_replied = (queue_name, priority, entry_count, message)
                self._current_generation_task = asyncio.create_task(self._generate_and_send(message))

                try:
                    await self._current_generation_task
                except asyncio.CancelledError:
                    logger.info(
                        f"[{self.stream_name}] Reply generation was interrupted externally for {queue_name} message. The message will be discarded."
                    )
                    # 被中断的消息应该被丢弃，而不是重新排队，以响应最新的用户输入。
                    # 旧的重新入队逻辑会导致所有中断的消息最终都被回复。

                except Exception as e:
                    logger.error(f"[{self.stream_name}] _generate_and_send task error: {e}", exc_info=True)
                finally:
                    self._current_generation_task = None
                    self._current_message_being_replied = None
                    # 标记任务完成
                    if queue_name == "vip":
                        self._vip_queue.task_done()
                    elif queue_name == "internal":
                        # 如果使用 internal_message 生成回复，则不从 normal 队列中移除
                        pass
                    else:
                        self._normal_queue.task_done()

                    # 检查是否还有任务，有则立即再次触发事件
                    if not self._vip_queue.empty() or not self._normal_queue.empty():
                        self._new_message_event.set()

            except asyncio.CancelledError:
                logger.info(f"[{self.stream_name}] Message processor is shutting down.")
                break
            except Exception as e:
                logger.error(f"[{self.stream_name}] Message processor main loop error: {e}", exc_info=True)
                await asyncio.sleep(1)
                
        
    def get_processing_message_id(self):
        self.last_msg_id = self.msg_id
        self.msg_id = f"{time.time()}_{random.randint(1000, 9999)}"


    async def _generate_and_send(self, message: MessageRecv):
        """为单个消息生成文本回复。整个过程可以被中断。"""
        self._is_replying = True
        total_chars_sent = 0  # 跟踪发送的总字符数
        
        self.get_processing_message_id()
        
        # 视线管理：开始生成回复时切换视线状态
        chat_watching = watching_manager.get_watching_by_chat_id(self.stream_id)
        
        if message.is_internal:
            await chat_watching.on_internal_message_start()
        else:
            await chat_watching.on_reply_start()

        sender_container = MessageSenderContainer(self.chat_stream, message)
        sender_container.start()

        async def generate_and_send_inner():
            nonlocal total_chars_sent
            logger.info(f"[S4U] 开始为消息生成文本和音频流: '{message.processed_plain_text[:30]}...'")

            if s4u_config.enable_streaming_output:
                logger.info("[S4U] 开始流式输出")
                # 流式输出，边生成边发送
                gen = self.gpt.generate_response(message, "")
                async for chunk in gen:
                    sender_container.msg_id = self.msg_id
                    await sender_container.add_message(chunk)
                    total_chars_sent += len(chunk)
            else:
                logger.info("[S4U] 开始一次性输出")
                # 一次性输出，先收集所有chunk
                all_chunks = []
                gen = self.gpt.generate_response(message, "")
                async for chunk in gen:
                    all_chunks.append(chunk)
                    total_chars_sent += len(chunk)
                # 一次性发送
                sender_container.msg_id = self.msg_id
                await sender_container.add_message("".join(all_chunks))

        try:
            try:
                await asyncio.wait_for(generate_and_send_inner(), timeout=10)
            except asyncio.TimeoutError:
                logger.warning(f"[{self.stream_name}] 回复生成超时，发送默认回复。")
                sender_container.msg_id = self.msg_id
                await sender_container.add_message("麦麦不知道哦")
                total_chars_sent = len("麦麦不知道哦")

            mood = mood_manager.get_mood_by_chat_id(self.stream_id)
            await yes_or_no_head(text = total_chars_sent,emotion = mood.mood_state,chat_history=message.processed_plain_text,chat_id=self.stream_id)

            # 等待所有文本消息发送完成
            await sender_container.close()
            await sender_container.join()
            
            await chat_watching.on_thinking_finished()
            
            
            
            start_time = time.time()
            logged = False
            while not self.go_processing():
                if time.time() - start_time > 60:
                    logger.warning(f"[{self.stream_name}] 等待消息发送超时（60秒），强制跳出循环。")
                    break
                if not logged:
                    logger.info(f"[{self.stream_name}] 等待消息发送完成...")
                    logged = True
                await asyncio.sleep(0.2)
            
            logger.info(f"[{self.stream_name}] 所有文本块处理完毕。")

        except asyncio.CancelledError:
            logger.info(f"[{self.stream_name}] 回复流程（文本）被中断。")
            raise  # 将取消异常向上传播
        except Exception as e:
            traceback.print_exc()
            logger.error(f"[{self.stream_name}] 回复生成过程中出现错误: {e}", exc_info=True)
            # 回复生成实时展示：清空内容（出错时）
        finally:
            self._is_replying = False
            
            # 视线管理：回复结束时切换视线状态
            chat_watching = watching_manager.get_watching_by_chat_id(self.stream_id)
            await chat_watching.on_reply_finished()
            
            # 确保发送器被妥善关闭（即使已关闭，再次调用也是安全的）
            sender_container.resume()
            if not sender_container._task.done():
                await sender_container.close()
                await sender_container.join()
            logger.info(f"[{self.stream_name}] _generate_and_send 任务结束，资源已清理。")

    async def shutdown(self):
        """平滑关闭处理任务。"""
        logger.info(f"正在关闭 S4UChat: {self.stream_name}")

        # 取消正在运行的任务
        if self._current_generation_task and not self._current_generation_task.done():
            self._current_generation_task.cancel()

        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()

        # 等待任务响应取消
        try:
            await self._processing_task
        except asyncio.CancelledError:
            logger.info(f"处理任务已成功取消: {self.stream_name}")
        
