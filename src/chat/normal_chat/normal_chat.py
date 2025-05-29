import asyncio
import time
import traceback
from random import random
from typing import List, Optional  # 导入 Optional
from maim_message import UserInfo, Seg
from src.common.logger_manager import get_logger
from src.chat.heart_flow.utils_chat import get_chat_type_and_target_info
from src.manager.mood_manager import mood_manager
from src.chat.message_receive.chat_stream import ChatStream, chat_manager
from src.person_info.relationship_manager import relationship_manager
from src.chat.utils.info_catcher import info_catcher_manager
from src.chat.utils.timer_calculator import Timer
from src.chat.utils.prompt_builder import global_prompt_manager
from .normal_chat_generator import NormalChatGenerator
from ..message_receive.message import MessageSending, MessageRecv, MessageThinking, MessageSet
from src.chat.message_receive.message_sender import message_manager
from src.chat.utils.utils_image import image_path_to_base64
from src.chat.emoji_system.emoji_manager import emoji_manager
from src.chat.normal_chat.willing.willing_manager import willing_manager
from src.chat.normal_chat.normal_chat_utils import get_recent_message_stats
from src.config.config import global_config

logger = get_logger("normal_chat")


class NormalChat:
    def __init__(self, chat_stream: ChatStream, interest_dict: dict = None, on_switch_to_focus_callback=None):
        """初始化 NormalChat 实例。只进行同步操作。"""

        self.chat_stream = chat_stream
        self.stream_id = chat_stream.stream_id
        self.stream_name = chat_manager.get_stream_name(self.stream_id) or self.stream_id

        # Interest dict
        self.interest_dict = interest_dict

        self.is_group_chat: bool = False
        self.chat_target_info: Optional[dict] = None

        self.willing_amplifier = 1
        self.start_time = time.time()

        # Other sync initializations
        self.gpt = NormalChatGenerator()
        self.mood_manager = mood_manager
        self.start_time = time.time()
        self._chat_task: Optional[asyncio.Task] = None
        self._initialized = False  # Track initialization status

        # 记录最近的回复内容，每项包含: {time, user_message, response, is_mentioned, is_reference_reply}
        self.recent_replies = []
        self.max_replies_history = 20  # 最多保存最近20条回复记录

        # 添加回调函数，用于在满足条件时通知切换到focus_chat模式
        self.on_switch_to_focus_callback = on_switch_to_focus_callback

        self._disabled = False  # 增加停用标志

    async def initialize(self):
        """异步初始化，获取聊天类型和目标信息。"""
        if self._initialized:
            return

        self.is_group_chat, self.chat_target_info = await get_chat_type_and_target_info(self.stream_id)
        self.stream_name = chat_manager.get_stream_name(self.stream_id) or self.stream_id
        self._initialized = True
        logger.debug(f"[{self.stream_name}] NormalChat 初始化完成 (异步部分)。")

    # 改为实例方法
    async def _create_thinking_message(self, message: MessageRecv, timestamp: Optional[float] = None) -> str:
        """创建思考消息"""
        messageinfo = message.message_info

        bot_user_info = UserInfo(
            user_id=global_config.bot.qq_account,
            user_nickname=global_config.bot.nickname,
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
            if global_config.experimental.debug_show_chat_mode:
                msg += "ⁿ"
            message_segment = Seg(type="text", data=msg)
            bot_message = MessageSending(
                message_id=thinking_id,
                chat_stream=self.chat_stream,  # 使用 self.chat_stream
                bot_user_info=UserInfo(
                    user_id=global_config.bot.qq_account,
                    user_nickname=global_config.bot.nickname,
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

        return first_bot_msg

    # 改为实例方法
    async def _handle_emoji(self, message: MessageRecv, response: str):
        """处理表情包"""
        if random() < global_config.normal_chat.emoji_chance:
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
                        user_id=global_config.bot.qq_account,
                        user_nickname=global_config.bot.nickname,
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
        self.mood_manager.update_mood_from_emotion(emotion, global_config.mood.mood_intensity_factor)

    async def _reply_interested_message(self) -> None:
        """
        后台任务方法，轮询当前实例关联chat的兴趣消息
        通常由start_monitoring_interest()启动
        """
        while True:
            async with global_prompt_manager.async_message_scope(self.chat_stream.context.get_template_name()):
                await asyncio.sleep(0.5)  # 每秒检查一次
                # 检查任务是否已被取消
                if self._chat_task is None or self._chat_task.cancelled():
                    logger.info(f"[{self.stream_name}] 兴趣监控任务被取消或置空，退出")
                    break

                items_to_process = list(self.interest_dict.items())
                if not items_to_process:
                    continue

                # 处理每条兴趣消息
                for msg_id, (message, interest_value, is_mentioned) in items_to_process:
                    try:
                        # 处理消息
                        if time.time() - self.start_time > 600:
                            self.adjust_reply_frequency(duration=600 / 60)
                        else:
                            self.adjust_reply_frequency(duration=(time.time() - self.start_time) / 60)

                        await self.normal_response(
                            message=message,
                            is_mentioned=is_mentioned,
                            interested_rate=interest_value * self.willing_amplifier,
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
        # 新增：如果已停用，直接返回
        if self._disabled:
            logger.info(f"[{self.stream_name}] 已停用，忽略 normal_response。")
            return

        timing_results = {}
        reply_probability = 1.0 if is_mentioned else 0.0  # 如果被提及，基础概率为1，否则需要意愿判断

        # 意愿管理器：设置当前message信息
        willing_manager.setup(message, self.chat_stream, is_mentioned, interested_rate)

        # 获取回复概率
        # is_willing = False
        # 仅在未被提及或基础概率不为1时查询意愿概率
        if reply_probability < 1:  # 简化逻辑，如果未提及 (reply_probability 为 0)，则获取意愿概率
            # is_willing = True
            reply_probability = await willing_manager.get_reply_probability(message.message_info.message_id)

            if message.message_info.additional_config:
                if "maimcore_reply_probability_gain" in message.message_info.additional_config.keys():
                    reply_probability += message.message_info.additional_config["maimcore_reply_probability_gain"]
                    reply_probability = min(max(reply_probability, 0), 1)  # 确保概率在 0-1 之间

        # 打印消息信息
        mes_name = self.chat_stream.group_info.group_name if self.chat_stream.group_info else "私聊"
        # current_time = time.strftime("%H:%M:%S", time.localtime(message.message_info.time))
        # 使用 self.stream_id
        # willing_log = f"[激活值:{await willing_manager.get_willing(self.stream_id):.2f}]" if is_willing else ""
        logger.info(
            f"[{mes_name}]"
            f"{message.message_info.user_info.user_nickname}:"  # 使用 self.chat_stream
            f"{message.processed_plain_text}[兴趣:{interested_rate:.2f}][回复概率:{reply_probability * 100:.1f}%]"
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

            # logger.info(f"[{self.stream_name}] 回复内容: {response_set}")

            if self._disabled:
                logger.info(f"[{self.stream_name}] 已停用，忽略 normal_response。")
                return

            # 发送回复 (不再需要传入 chat)
            with Timer("消息发送", timing_results):
                first_bot_msg = await self._add_messages_to_manager(message, response_set, thinking_id)

            # 检查 first_bot_msg 是否为 None (例如思考消息已被移除的情况)
            if first_bot_msg:
                info_catcher.catch_after_response(timing_results["消息发送"], response_set, first_bot_msg)

                # 记录回复信息到最近回复列表中
                reply_info = {
                    "time": time.time(),
                    "user_message": message.processed_plain_text,
                    "user_info": {
                        "user_id": message.message_info.user_info.user_id,
                        "user_nickname": message.message_info.user_info.user_nickname,
                    },
                    "response": response_set,
                    "is_mentioned": is_mentioned,
                    "is_reference_reply": message.reply is not None,  # 判断是否为引用回复
                    "timing": {k: round(v, 2) for k, v in timing_results.items()},
                }
                self.recent_replies.append(reply_info)
                # 保持最近回复历史在限定数量内
                if len(self.recent_replies) > self.max_replies_history:
                    self.recent_replies = self.recent_replies[-self.max_replies_history :]

                # 检查是否需要切换到focus模式
                if global_config.chat.chat_mode == "auto":
                    await self._check_switch_to_focus()

            info_catcher.done_catch()

            with Timer("处理表情包", timing_results):
                await self._handle_emoji(message, response_set[0])

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
                f"[{self.stream_name}]回复消息: {trigger_msg[:30]}... | 回复内容: {response_msg[:30]}... | 计时: {timing_str}"
            )
        elif not do_reply:
            # 不回复处理
            await willing_manager.not_reply_handle(message.message_info.message_id)

        # 意愿管理器：注销当前message信息 (无论是否回复，只要处理过就删除)
        willing_manager.delete(message.message_info.message_id)

    # 改为实例方法, 移除 chat 参数

    async def start_chat(self):
        """先进行异步初始化，然后启动聊天任务。"""
        if not self._initialized:
            await self.initialize()  # Ensure initialized before starting tasks

        self._disabled = False  # 启动时重置停用标志

        if self._chat_task is None or self._chat_task.done():
            # logger.info(f"[{self.stream_name}] 开始处理兴趣消息...")
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
                traceback.print_exc()
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
        self._disabled = True  # 停止时设置停用标志
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
            traceback.print_exc()

    # 获取最近回复记录的方法
    def get_recent_replies(self, limit: int = 10) -> List[dict]:
        """获取最近的回复记录

        Args:
            limit: 最大返回数量，默认10条

        Returns:
            List[dict]: 最近的回复记录列表，每项包含：
                time: 回复时间戳
                user_message: 用户消息内容
                user_info: 用户信息(user_id, user_nickname)
                response: 回复内容
                is_mentioned: 是否被提及(@)
                is_reference_reply: 是否为引用回复
                timing: 各阶段耗时
        """
        # 返回最近的limit条记录，按时间倒序排列
        return sorted(self.recent_replies[-limit:], key=lambda x: x["time"], reverse=True)

    async def _check_switch_to_focus(self) -> None:
        """检查是否满足切换到focus模式的条件"""
        if not self.on_switch_to_focus_callback:
            return  # 如果没有设置回调函数，直接返回
        current_time = time.time()

        time_threshold = 120 / global_config.chat.auto_focus_threshold
        reply_threshold = 6 * global_config.chat.auto_focus_threshold

        one_minute_ago = current_time - time_threshold

        # 统计1分钟内的回复数量
        recent_reply_count = sum(1 for reply in self.recent_replies if reply["time"] > one_minute_ago)
        if recent_reply_count > reply_threshold:
            logger.info(
                f"[{self.stream_name}] 检测到1分钟内回复数量({recent_reply_count})大于{reply_threshold}，触发切换到focus模式"
            )
            try:
                # 调用回调函数通知上层切换到focus模式
                await self.on_switch_to_focus_callback()
            except Exception as e:
                logger.error(f"[{self.stream_name}] 触发切换到focus模式时出错: {e}\n{traceback.format_exc()}")

    def adjust_reply_frequency(self, duration: int = 10):
        """
        调整回复频率
        """
        # 获取最近30分钟内的消息统计

        stats = get_recent_message_stats(minutes=duration, chat_id=self.stream_id)
        bot_reply_count = stats["bot_reply_count"]

        total_message_count = stats["total_message_count"]
        if total_message_count == 0:
            return
        logger.debug(
            f"[{self.stream_name}]({self.willing_amplifier}) 最近{duration}分钟 回复数量: {bot_reply_count}，消息总数: {total_message_count}"
        )

        # 计算回复频率
        _reply_frequency = bot_reply_count / total_message_count

        differ = global_config.normal_chat.talk_frequency - (bot_reply_count / duration)

        # 如果回复频率低于0.5，增加回复概率
        if differ > 0.1:
            mapped = 1 + (differ - 0.1) * 4 / 0.9
            mapped = max(1, min(5, mapped))
            logger.info(
                f"[{self.stream_name}] 回复频率低于{global_config.normal_chat.talk_frequency}，增加回复概率，differ={differ:.3f}，映射值={mapped:.2f}"
            )
            self.willing_amplifier += mapped * 0.1  # 你可以根据实际需要调整系数
        elif differ < -0.1:
            mapped = 1 - (differ + 0.1) * 4 / 0.9
            mapped = max(1, min(5, mapped))
            logger.info(
                f"[{self.stream_name}] 回复频率高于{global_config.normal_chat.talk_frequency}，减少回复概率，differ={differ:.3f}，映射值={mapped:.2f}"
            )
            self.willing_amplifier -= mapped * 0.1

        if self.willing_amplifier > 5:
            self.willing_amplifier = 5
        elif self.willing_amplifier < 0.1:
            self.willing_amplifier = 0.1
