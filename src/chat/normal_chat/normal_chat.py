import asyncio
import statistics  # 导入 statistics 模块
import time
import traceback
from random import random
from typing import List, Optional  # 导入 Optional

from maim_message import UserInfo, Seg

from src.common.logger_manager import get_logger
from src.chat.heart_flow.utils_chat import get_chat_type_and_target_info
from src.manager.mood_manager import mood_manager
from src.chat.message_receive.chat_stream import ChatStream, chat_manager
from src.chat.person_info.relationship_manager import relationship_manager
from src.chat.utils.info_catcher import info_catcher_manager
from src.chat.utils.timer_calculator import Timer
from .normal_chat_generator import NormalChatGenerator
from ..message_receive.message import MessageSending, MessageRecv, MessageThinking, MessageSet
from src.chat.message_receive.message_sender import message_manager
from src.chat.utils.utils_image import image_path_to_base64
from src.chat.emoji_system.emoji_manager import emoji_manager
from src.chat.normal_chat.willing.willing_manager import willing_manager
from src.config.config import global_config
from src.plugins.group_nickname.nickname_manager import nickname_manager

logger = get_logger("chat")


class NormalChat:
    def __init__(self, chat_stream: ChatStream, interest_dict: dict = None):
        """初始化 NormalChat 实例。只进行同步操作。"""

        # Basic info from chat_stream (sync)
        self.chat_stream = chat_stream
        self.stream_id = chat_stream.stream_id
        # Get initial stream name, might be updated in initialize
        self.stream_name = chat_manager.get_stream_name(self.stream_id) or self.stream_id

        # Interest dict
        self.interest_dict = interest_dict

        # --- Initialize attributes (defaults) ---
        self.is_group_chat: bool = False
        self.chat_target_info: Optional[dict] = None
        # --- End Initialization ---

        # Other sync initializations
        self.gpt = NormalChatGenerator()
        self.mood_manager = mood_manager
        self.start_time = time.time()
        self.last_speak_time = 0
        self._chat_task: Optional[asyncio.Task] = None
        self._initialized = False  # Track initialization status

        # logger.info(f"[{self.stream_name}] NormalChat 实例 __init__ 完成 (同步部分)。")
        # Avoid logging here as stream_name might not be final

    async def initialize(self):
        """异步初始化，获取聊天类型和目标信息。"""
        if self._initialized:
            return

        # --- Use utility function to determine chat type and fetch info ---
        self.is_group_chat, self.chat_target_info = await get_chat_type_and_target_info(self.stream_id)
        # Update stream_name again after potential async call in util func
        self.stream_name = chat_manager.get_stream_name(self.stream_id) or self.stream_id
        # --- End using utility function ---
        self._initialized = True
        logger.info(f"[{self.stream_name}] NormalChat 实例 initialize 完成 (异步部分)。")

    # 改为实例方法
    async def _create_thinking_message(self, message: MessageRecv, timestamp: Optional[float] = None) -> str:
        """创建思考消息"""
        messageinfo = message.message_info

        bot_user_info = UserInfo(
            user_id=global_config.BOT_QQ,
            user_nickname=global_config.BOT_NICKNAME,
            platform=messageinfo.platform,
        )

        thinking_time_point = round(time.time(), 2)
        thinking_id = "mt" + str(thinking_time_point)
        thinking_message = MessageThinking(
            message_id=thinking_id,
            chat_stream=self.chat_stream,
            bot_user_info=bot_user_info,
            reply=message,
            thinking_start_time=thinking_time_point,
            timestamp=timestamp if timestamp is not None else None,
        )

        await message_manager.add_message(thinking_message)
        return thinking_id

    # 改为实例方法
    async def _add_messages_to_manager(
        self, message: MessageRecv, response_set: List[str], thinking_id
    ) -> Optional[MessageSending]:
        """发送回复消息"""
        container = await message_manager.get_container(self.stream_id)  # 使用 self.stream_id
        thinking_message = None

        for msg in container.messages[:]:
            if isinstance(msg, MessageThinking) and msg.message_info.message_id == thinking_id:
                thinking_message = msg
                container.messages.remove(msg)
                break

        if not thinking_message:
            logger.warning(f"[{self.stream_name}] 未找到对应的思考消息 {thinking_id}，可能已超时被移除")
            return None

        thinking_start_time = thinking_message.thinking_start_time
        message_set = MessageSet(self.chat_stream, thinking_id)  # 使用 self.chat_stream

        mark_head = False
        first_bot_msg = None
        for msg in response_set:
            message_segment = Seg(type="text", data=msg)
            bot_message = MessageSending(
                message_id=thinking_id,
                chat_stream=self.chat_stream,  # 使用 self.chat_stream
                bot_user_info=UserInfo(
                    user_id=global_config.BOT_QQ,
                    user_nickname=global_config.BOT_NICKNAME,
                    platform=message.message_info.platform,
                ),
                sender_info=message.message_info.user_info,
                message_segment=message_segment,
                reply=message,
                is_head=not mark_head,
                is_emoji=False,
                thinking_start_time=thinking_start_time,
                apply_set_reply_logic=True,
            )
            if not mark_head:
                mark_head = True
                first_bot_msg = bot_message
            message_set.add_message(bot_message)

        await message_manager.add_message(message_set)

        self.last_speak_time = time.time()

        return first_bot_msg

    # 改为实例方法
    async def _handle_emoji(self, message: MessageRecv, response: str):
        """处理表情包"""
        if random() < global_config.emoji_chance:
            emoji_raw = await emoji_manager.get_emoji_for_text(response)
            if emoji_raw:
                emoji_path, description = emoji_raw
                emoji_cq = image_path_to_base64(emoji_path)

                thinking_time_point = round(message.message_info.time, 2)

                message_segment = Seg(type="emoji", data=emoji_cq)
                bot_message = MessageSending(
                    message_id="mt" + str(thinking_time_point),
                    chat_stream=self.chat_stream,  # 使用 self.chat_stream
                    bot_user_info=UserInfo(
                        user_id=global_config.BOT_QQ,
                        user_nickname=global_config.BOT_NICKNAME,
                        platform=message.message_info.platform,
                    ),
                    sender_info=message.message_info.user_info,
                    message_segment=message_segment,
                    reply=message,
                    is_head=False,
                    is_emoji=True,
                    apply_set_reply_logic=True,
                )
                await message_manager.add_message(bot_message)

    # 改为实例方法 (虽然它只用 message.chat_stream, 但逻辑上属于实例)
    async def _update_relationship(self, message: MessageRecv, response_set):
        """更新关系情绪"""
        ori_response = ",".join(response_set)
        stance, emotion = await self.gpt._get_emotion_tags(ori_response, message.processed_plain_text)
        user_info = message.message_info.user_info
        platform = user_info.platform
        await relationship_manager.calculate_update_relationship_value(
            user_info,
            platform,
            label=emotion,
            stance=stance,  # 使用 self.chat_stream
        )
        self.mood_manager.update_mood_from_emotion(emotion, global_config.mood_intensity_factor)

    async def _reply_interested_message(self) -> None:
        """
        后台任务方法，轮询当前实例关联chat的兴趣消息
        通常由start_monitoring_interest()启动
        """
        while True:
            await asyncio.sleep(0.5)  # 每秒检查一次
            # 检查任务是否已被取消
            if self._chat_task is None or self._chat_task.cancelled():
                logger.info(f"[{self.stream_name}] 兴趣监控任务被取消或置空，退出")
                break

            # 获取待处理消息列表
            items_to_process = list(self.interest_dict.items())
            if not items_to_process:
                continue

            # 处理每条兴趣消息
            for msg_id, (message, interest_value, is_mentioned) in items_to_process:
                try:
                    # 处理消息
                    await self.normal_response(
                        message=message,
                        is_mentioned=is_mentioned,
                        interested_rate=interest_value,
                        rewind_response=False,
                    )
                except Exception as e:
                    logger.error(f"[{self.stream_name}] 处理兴趣消息{msg_id}时出错: {e}\n{traceback.format_exc()}")
                finally:
                    self.interest_dict.pop(msg_id, None)

    # 改为实例方法, 移除 chat 参数
    async def normal_response(
        self, message: MessageRecv, is_mentioned: bool, interested_rate: float, rewind_response: bool = False
    ) -> None:
        # 检查收到的消息是否属于当前实例处理的 chat stream
        if message.chat_stream.stream_id != self.stream_id:
            logger.error(
                f"[{self.stream_name}] normal_response 收到不匹配的消息 (来自 {message.chat_stream.stream_id})，预期 {self.stream_id}。已忽略。"
            )
            return

        timing_results = {}

        reply_probability = 1.0 if is_mentioned else 0.0  # 如果被提及，基础概率为1，否则需要意愿判断

        # 意愿管理器：设置当前message信息

        willing_manager.setup(message, self.chat_stream, is_mentioned, interested_rate)

        # 获取回复概率
        is_willing = False
        # 仅在未被提及或基础概率不为1时查询意愿概率
        if reply_probability < 1:  # 简化逻辑，如果未提及 (reply_probability 为 0)，则获取意愿概率
            is_willing = True
            reply_probability = await willing_manager.get_reply_probability(message.message_info.message_id)

            if message.message_info.additional_config:
                if "maimcore_reply_probability_gain" in message.message_info.additional_config.keys():
                    reply_probability += message.message_info.additional_config["maimcore_reply_probability_gain"]
                    reply_probability = min(max(reply_probability, 0), 1)  # 确保概率在 0-1 之间

        # 打印消息信息
        mes_name = self.chat_stream.group_info.group_name if self.chat_stream.group_info else "私聊"
        current_time = time.strftime("%H:%M:%S", time.localtime(message.message_info.time))
        # 使用 self.stream_id
        willing_log = f"[回复意愿:{await willing_manager.get_willing(self.stream_id):.2f}]" if is_willing else ""
        logger.info(
            f"[{current_time}][{mes_name}]"
            f"{message.message_info.user_info.user_nickname}:"  # 使用 self.chat_stream
            f"{message.processed_plain_text}{willing_log}[概率:{reply_probability * 100:.1f}%]"
        )
        do_reply = False
        response_set = None  # 初始化 response_set
        if random() < reply_probability:
            do_reply = True

            # 回复前处理
            await willing_manager.before_generate_reply_handle(message.message_info.message_id)

            with Timer("创建思考消息", timing_results):
                if rewind_response:
                    thinking_id = await self._create_thinking_message(message, message.message_info.time)
                else:
                    thinking_id = await self._create_thinking_message(message)

            logger.debug(f"[{self.stream_name}] 创建捕捉器，thinking_id:{thinking_id}")

            info_catcher = info_catcher_manager.get_info_catcher(thinking_id)
            info_catcher.catch_decide_to_response(message)

            try:
                with Timer("生成回复", timing_results):
                    response_set = await self.gpt.generate_response(
                        message=message,
                        thinking_id=thinking_id,
                    )

                info_catcher.catch_after_generate_response(timing_results["生成回复"])
            except Exception as e:
                logger.error(f"[{self.stream_name}] 回复生成出现错误：{str(e)} {traceback.format_exc()}")
                response_set = None  # 确保出错时 response_set 为 None

            if not response_set:
                logger.info(f"[{self.stream_name}] 模型未生成回复内容")
                # 如果模型未生成回复，移除思考消息
                container = await message_manager.get_container(self.stream_id)  # 使用 self.stream_id
                for msg in container.messages[:]:
                    if isinstance(msg, MessageThinking) and msg.message_info.message_id == thinking_id:
                        container.messages.remove(msg)
                        logger.debug(f"[{self.stream_name}] 已移除未产生回复的思考消息 {thinking_id}")
                        break
                # 需要在此处也调用 not_reply_handle 和 delete 吗？
                # 如果是因为模型没回复，也算是一种 "未回复"
                await willing_manager.not_reply_handle(message.message_info.message_id)
                willing_manager.delete(message.message_info.message_id)
                return  # 不执行后续步骤

            logger.info(f"[{self.stream_name}] 回复内容: {response_set}")

            # 发送回复 (不再需要传入 chat)
            with Timer("消息发送", timing_results):
                first_bot_msg = await self._add_messages_to_manager(message, response_set, thinking_id)

            # 检查 first_bot_msg 是否为 None (例如思考消息已被移除的情况)
            if first_bot_msg:
                info_catcher.catch_after_response(timing_results["消息发送"], response_set, first_bot_msg)
                await nickname_manager.trigger_nickname_analysis(message, response_set, self.chat_stream)
            else:
                logger.warning(f"[{self.stream_name}] 思考消息 {thinking_id} 在发送前丢失，无法记录 info_catcher")

            info_catcher.done_catch()

            # 处理表情包 (不再需要传入 chat)
            with Timer("处理表情包", timing_results):
                await self._handle_emoji(message, response_set[0])

            # 更新关系情绪 (不再需要传入 chat)
            with Timer("关系更新", timing_results):
                await self._update_relationship(message, response_set)

            # 回复后处理
            await willing_manager.after_generate_reply_handle(message.message_info.message_id)

        # 输出性能计时结果
        if do_reply and response_set:  # 确保 response_set 不是 None
            timing_str = " | ".join([f"{step}: {duration:.2f}秒" for step, duration in timing_results.items()])
            trigger_msg = message.processed_plain_text
            response_msg = " ".join(response_set)
            logger.info(
                f"[{self.stream_name}] 触发消息: {trigger_msg[:20]}... | 推理消息: {response_msg[:20]}... | 性能计时: {timing_str}"
            )
        elif not do_reply:
            # 不回复处理
            await willing_manager.not_reply_handle(message.message_info.message_id)
        # else: # do_reply is True but response_set is None (handled above)
        # logger.info(f"[{self.stream_name}] 决定回复但模型未生成内容。触发: {message.processed_plain_text[:20]}...")

        # 意愿管理器：注销当前message信息 (无论是否回复，只要处理过就删除)
        willing_manager.delete(message.message_info.message_id)

    # --- 新增：处理初始高兴趣消息的私有方法 ---
    async def _process_initial_interest_messages(self):
        """处理启动时存在于 interest_dict 中的高兴趣消息。"""
        if not self.interest_dict:
            return  # 如果 interest_dict 为 None 或空，直接返回

        items_to_process = list(self.interest_dict.items())
        if not items_to_process:
            return  # 没有初始消息，直接返回

        logger.info(f"[{self.stream_name}] 发现 {len(items_to_process)} 条初始兴趣消息，开始处理高兴趣部分...")
        interest_values = [item[1][1] for item in items_to_process]  # 提取兴趣值列表

        messages_to_reply = []  # 需要立即回复的消息

        if len(interest_values) == 1:
            # 如果只有一个消息，直接处理
            messages_to_reply.append(items_to_process[0])
            logger.info(f"[{self.stream_name}] 只有一条初始消息，直接处理。")
        elif len(interest_values) > 1:
            # 计算均值和标准差
            try:
                mean_interest = statistics.mean(interest_values)
                stdev_interest = statistics.stdev(interest_values)
                threshold = mean_interest + stdev_interest
                logger.info(
                    f"[{self.stream_name}] 初始兴趣值 均值: {mean_interest:.2f}, 标准差: {stdev_interest:.2f}, 阈值: {threshold:.2f}"
                )

                # 找出高于阈值的消息
                for item in items_to_process:
                    msg_id, (message, interest_value, is_mentioned) = item
                    if interest_value > threshold:
                        messages_to_reply.append(item)
                logger.info(f"[{self.stream_name}] 找到 {len(messages_to_reply)} 条高于阈值的初始消息进行处理。")
            except statistics.StatisticsError as e:
                logger.error(f"[{self.stream_name}] 计算初始兴趣统计值时出错: {e}，跳过初始处理。")

        # 处理需要回复的消息
        processed_count = 0
        # --- 修改：迭代前创建要处理的ID列表副本，防止迭代时修改 ---
        messages_to_process_initially = list(messages_to_reply)  # 创建副本
        # --- 新增：限制最多处理两条消息 ---
        messages_to_process_initially = messages_to_process_initially[:2]
        # --- 新增结束 ---
        for item in messages_to_process_initially:  # 使用副本迭代
            msg_id, (message, interest_value, is_mentioned) = item
            # --- 修改：在处理前尝试 pop，防止竞争 ---
            popped_item = self.interest_dict.pop(msg_id, None)
            if popped_item is None:
                logger.warning(f"[{self.stream_name}] 初始兴趣消息 {msg_id} 在处理前已被移除，跳过。")
                continue  # 如果消息已被其他任务处理（pop），则跳过
            # --- 修改结束 ---

            try:
                logger.info(f"[{self.stream_name}] 处理初始高兴趣消息 {msg_id} (兴趣值: {interest_value:.2f})")
                await self.normal_response(
                    message=message, is_mentioned=is_mentioned, interested_rate=interest_value, rewind_response=True
                )
                processed_count += 1
            except Exception as e:
                logger.error(f"[{self.stream_name}] 处理初始兴趣消息 {msg_id} 时出错: {e}\\n{traceback.format_exc()}")

        # --- 新增：处理完后清空整个字典 ---
        logger.info(
            f"[{self.stream_name}] 处理了 {processed_count} 条初始高兴趣消息。现在清空所有剩余的初始兴趣消息..."
        )
        self.interest_dict.clear()
        # --- 新增结束 ---

        logger.info(
            f"[{self.stream_name}] 初始高兴趣消息处理完毕，共处理 {processed_count} 条。剩余 {len(self.interest_dict)} 条待轮询。"
        )

    # --- 新增结束 ---

    # 保持 staticmethod, 因为不依赖实例状态, 但需要 chat 对象来获取日志上下文
    @staticmethod
    def _check_ban_words(text: str, chat: ChatStream, userinfo: UserInfo) -> bool:
        """检查消息中是否包含过滤词"""
        stream_name = chat_manager.get_stream_name(chat.stream_id) or chat.stream_id
        for word in global_config.ban_words:
            if word in text:
                logger.info(
                    f"[{stream_name}][{chat.group_info.group_name if chat.group_info else '私聊'}]"
                    f"{userinfo.user_nickname}:{text}"
                )
                logger.info(f"[{stream_name}][过滤词识别] 消息中含有 '{word}'，filtered")
                return True
        return False

    # 保持 staticmethod, 因为不依赖实例状态, 但需要 chat 对象来获取日志上下文
    @staticmethod
    def _check_ban_regex(text: str, chat: ChatStream, userinfo: UserInfo) -> bool:
        """检查消息是否匹配过滤正则表达式"""
        stream_name = chat_manager.get_stream_name(chat.stream_id) or chat.stream_id
        for pattern in global_config.ban_msgs_regex:
            if pattern.search(text):
                logger.info(
                    f"[{stream_name}][{chat.group_info.group_name if chat.group_info else '私聊'}]"
                    f"{userinfo.user_nickname}:{text}"
                )
                logger.info(f"[{stream_name}][正则表达式过滤] 消息匹配到 '{pattern.pattern}'，filtered")
                return True
        return False

    # 改为实例方法, 移除 chat 参数

    async def start_chat(self):
        """先进行异步初始化，然后启动聊天任务。"""
        if not self._initialized:
            await self.initialize()  # Ensure initialized before starting tasks

        if self._chat_task is None or self._chat_task.done():
            logger.info(f"[{self.stream_name}] 开始后台处理初始兴趣消息和轮询任务...")
            # Process initial messages first
            await self._process_initial_interest_messages()
            # Then start polling task
            polling_task = asyncio.create_task(self._reply_interested_message())
            polling_task.add_done_callback(lambda t: self._handle_task_completion(t))
            self._chat_task = polling_task
        else:
            logger.info(f"[{self.stream_name}] 聊天轮询任务已在运行中。")

    def _handle_task_completion(self, task: asyncio.Task):
        """任务完成回调处理"""
        if task is not self._chat_task:
            logger.warning(f"[{self.stream_name}] 收到未知任务回调")
            return
        try:
            if exc := task.exception():
                logger.error(f"[{self.stream_name}] 任务异常: {exc}")
                logger.error(traceback.format_exc())
        except asyncio.CancelledError:
            logger.debug(f"[{self.stream_name}] 任务已取消")
        except Exception as e:
            logger.error(f"[{self.stream_name}] 回调处理错误: {e}")
        finally:
            if self._chat_task is task:
                self._chat_task = None
                logger.debug(f"[{self.stream_name}] 任务清理完成")

    # 改为实例方法, 移除 stream_id 参数
    async def stop_chat(self):
        """停止当前实例的兴趣监控任务。"""
        if self._chat_task and not self._chat_task.done():
            task = self._chat_task
            logger.debug(f"[{self.stream_name}] 尝试取消normal聊天任务。")
            task.cancel()
            try:
                await task  # 等待任务响应取消
            except asyncio.CancelledError:
                logger.info(f"[{self.stream_name}] 结束一般聊天模式。")
            except Exception as e:
                # 回调函数 _handle_task_completion 会处理异常日志
                logger.warning(f"[{self.stream_name}] 等待监控任务取消时捕获到异常 (可能已在回调中记录): {e}")
            finally:
                # 确保任务状态更新，即使等待出错 (回调函数也会尝试更新)
                if self._chat_task is task:
                    self._chat_task = None

        # 清理所有未处理的思考消息
        try:
            container = await message_manager.get_container(self.stream_id)
            if container:
                # 查找并移除所有 MessageThinking 类型的消息
                thinking_messages = [msg for msg in container.messages[:] if isinstance(msg, MessageThinking)]
                if thinking_messages:
                    for msg in thinking_messages:
                        container.messages.remove(msg)
                    logger.info(f"[{self.stream_name}] 清理了 {len(thinking_messages)} 条未处理的思考消息。")
        except Exception as e:
            logger.error(f"[{self.stream_name}] 清理思考消息时出错: {e}")
            logger.error(traceback.format_exc())
