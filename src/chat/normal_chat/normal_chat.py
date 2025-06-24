import asyncio
import time
import traceback
from random import random
from typing import List, Optional, Dict  # 导入类型提示
import os
import pickle
from maim_message import UserInfo, Seg
from src.common.logger import get_logger
from src.chat.heart_flow.utils_chat import get_chat_type_and_target_info
from src.manager.mood_manager import mood_manager
from src.chat.message_receive.chat_stream import ChatStream, get_chat_manager
from src.chat.utils.timer_calculator import Timer
from src.chat.utils.prompt_builder import global_prompt_manager
from .normal_chat_generator import NormalChatGenerator
from ..message_receive.message import MessageSending, MessageRecv, MessageThinking, MessageSet
from src.chat.message_receive.message_sender import message_manager
from src.chat.normal_chat.willing.willing_manager import get_willing_manager
from src.chat.normal_chat.normal_chat_utils import get_recent_message_stats
from src.config.config import global_config
from src.chat.focus_chat.planners.action_manager import ActionManager
from src.chat.normal_chat.normal_chat_planner import NormalChatPlanner
from src.chat.normal_chat.normal_chat_action_modifier import NormalChatActionModifier
from src.chat.normal_chat.normal_chat_expressor import NormalChatExpressor
from src.chat.focus_chat.replyer.default_generator import DefaultReplyer
from src.person_info.person_info import PersonInfoManager
from src.person_info.relationship_manager import get_relationship_manager
from src.chat.utils.chat_message_builder import (
    get_raw_msg_by_timestamp_with_chat,
    get_raw_msg_by_timestamp_with_chat_inclusive,
    get_raw_msg_before_timestamp_with_chat,
    num_new_messages_since,
)

willing_manager = get_willing_manager()

logger = get_logger("normal_chat")

# 消息段清理配置
SEGMENT_CLEANUP_CONFIG = {
    "enable_cleanup": True,  # 是否启用清理
    "max_segment_age_days": 7,  # 消息段最大保存天数
    "max_segments_per_user": 10,  # 每用户最大消息段数
    "cleanup_interval_hours": 1,  # 清理间隔（小时）
}


class NormalChat:
    def __init__(self, chat_stream: ChatStream, interest_dict: dict = None, on_switch_to_focus_callback=None):
        """初始化 NormalChat 实例。只进行同步操作。"""

        self.chat_stream = chat_stream
        self.stream_id = chat_stream.stream_id
        self.stream_name = get_chat_manager().get_stream_name(self.stream_id) or self.stream_id

        # 初始化Normal Chat专用表达器
        self.expressor = NormalChatExpressor(self.chat_stream)
        self.replyer = DefaultReplyer(self.chat_stream)

        # Interest dict
        self.interest_dict = interest_dict

        self.is_group_chat, self.chat_target_info = get_chat_type_and_target_info(self.stream_id)

        self.willing_amplifier = 1
        self.start_time = time.time()

        # Other sync initializations
        self.gpt = NormalChatGenerator()
        self.mood_manager = mood_manager
        self.start_time = time.time()
        self._chat_task: Optional[asyncio.Task] = None
        self._initialized = False  # Track initialization status

        # Planner相关初始化
        self.action_manager = ActionManager()
        self.planner = NormalChatPlanner(self.stream_name, self.action_manager)
        self.action_modifier = NormalChatActionModifier(self.action_manager, self.stream_id, self.stream_name)
        self.enable_planner = global_config.normal_chat.enable_planner  # 从配置中读取是否启用planner

        # 记录最近的回复内容，每项包含: {time, user_message, response, is_mentioned, is_reference_reply}
        self.recent_replies = []
        self.max_replies_history = 20  # 最多保存最近20条回复记录

        # 新的消息段缓存结构：
        # {person_id: [{"start_time": float, "end_time": float, "last_msg_time": float, "message_count": int}, ...]}
        self.person_engaged_cache: Dict[str, List[Dict[str, any]]] = {}

        # 持久化存储文件路径
        self.cache_file_path = os.path.join("data", "relationship", f"relationship_cache_{self.stream_id}.pkl")

        # 最后处理的消息时间，避免重复处理相同消息
        self.last_processed_message_time = 0.0

        # 最后清理时间，用于定期清理老消息段
        self.last_cleanup_time = 0.0

        # 添加回调函数，用于在满足条件时通知切换到focus_chat模式
        self.on_switch_to_focus_callback = on_switch_to_focus_callback

        self._disabled = False  # 增加停用标志

        # 加载持久化的缓存
        self._load_cache()

        logger.debug(f"[{self.stream_name}] NormalChat 初始化完成 (异步部分)。")

    # ================================
    # 缓存管理模块
    # 负责持久化存储、状态管理、缓存读写
    # ================================

    def _load_cache(self):
        """从文件加载持久化的缓存"""
        if os.path.exists(self.cache_file_path):
            try:
                with open(self.cache_file_path, "rb") as f:
                    cache_data = pickle.load(f)
                    # 新格式：包含额外信息的缓存
                    self.person_engaged_cache = cache_data.get("person_engaged_cache", {})
                    self.last_processed_message_time = cache_data.get("last_processed_message_time", 0.0)
                    self.last_cleanup_time = cache_data.get("last_cleanup_time", 0.0)

                logger.info(
                    f"[{self.stream_name}] 成功加载关系缓存，包含 {len(self.person_engaged_cache)} 个用户，最后处理时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.last_processed_message_time)) if self.last_processed_message_time > 0 else '未设置'}"
                )
            except Exception as e:
                logger.error(f"[{self.stream_name}] 加载关系缓存失败: {e}")
                self.person_engaged_cache = {}
                self.last_processed_message_time = 0.0
        else:
            logger.info(f"[{self.stream_name}] 关系缓存文件不存在，使用空缓存")

    def _save_cache(self):
        """保存缓存到文件"""
        try:
            os.makedirs(os.path.dirname(self.cache_file_path), exist_ok=True)
            cache_data = {
                "person_engaged_cache": self.person_engaged_cache,
                "last_processed_message_time": self.last_processed_message_time,
                "last_cleanup_time": self.last_cleanup_time,
            }
            with open(self.cache_file_path, "wb") as f:
                pickle.dump(cache_data, f)
            logger.debug(f"[{self.stream_name}] 成功保存关系缓存")
        except Exception as e:
            logger.error(f"[{self.stream_name}] 保存关系缓存失败: {e}")

    # ================================
    # 消息段管理模块
    # 负责跟踪用户消息活动、管理消息段、清理过期数据
    # ================================

    def _update_message_segments(self, person_id: str, message_time: float):
        """更新用户的消息段

        Args:
            person_id: 用户ID
            message_time: 消息时间戳
        """
        if person_id not in self.person_engaged_cache:
            self.person_engaged_cache[person_id] = []

        segments = self.person_engaged_cache[person_id]
        current_time = time.time()

        # 获取该消息前5条消息的时间作为潜在的开始时间
        before_messages = get_raw_msg_before_timestamp_with_chat(self.stream_id, message_time, limit=5)
        if before_messages:
            # 由于get_raw_msg_before_timestamp_with_chat返回按时间升序排序的消息，最后一个是最接近message_time的
            # 我们需要第一个消息作为开始时间，但应该确保至少包含5条消息或该用户之前的消息
            potential_start_time = before_messages[0]["time"]
        else:
            # 如果没有前面的消息，就从当前消息开始
            potential_start_time = message_time

        # 如果没有现有消息段，创建新的
        if not segments:
            new_segment = {
                "start_time": potential_start_time,
                "end_time": message_time,
                "last_msg_time": message_time,
                "message_count": self._count_messages_in_timerange(potential_start_time, message_time),
            }
            segments.append(new_segment)
            logger.info(
                f"[{self.stream_name}] 为用户 {person_id} 创建新消息段: 时间范围 {time.strftime('%H:%M:%S', time.localtime(potential_start_time))} - {time.strftime('%H:%M:%S', time.localtime(message_time))}, 消息数: {new_segment['message_count']}"
            )
            self._save_cache()
            return

        # 获取最后一个消息段
        last_segment = segments[-1]

        # 计算从最后一条消息到当前消息之间的消息数量（不包含边界）
        messages_between = self._count_messages_between(last_segment["last_msg_time"], message_time)

        if messages_between <= 10:
            # 在10条消息内，延伸当前消息段
            last_segment["end_time"] = message_time
            last_segment["last_msg_time"] = message_time
            # 重新计算整个消息段的消息数量
            last_segment["message_count"] = self._count_messages_in_timerange(
                last_segment["start_time"], last_segment["end_time"]
            )
            logger.debug(f"[{self.stream_name}] 延伸用户 {person_id} 的消息段: {last_segment}")
        else:
            # 超过10条消息，结束当前消息段并创建新的
            # 结束当前消息段：延伸到原消息段最后一条消息后5条消息的时间
            after_messages = get_raw_msg_by_timestamp_with_chat(
                self.stream_id, last_segment["last_msg_time"], current_time, limit=5, limit_mode="earliest"
            )
            if after_messages and len(after_messages) >= 5:
                # 如果有足够的后续消息，使用第5条消息的时间作为结束时间
                last_segment["end_time"] = after_messages[4]["time"]
            else:
                # 如果没有足够的后续消息，保持原有的结束时间
                pass

            # 重新计算当前消息段的消息数量
            last_segment["message_count"] = self._count_messages_in_timerange(
                last_segment["start_time"], last_segment["end_time"]
            )

            # 创建新的消息段
            new_segment = {
                "start_time": potential_start_time,
                "end_time": message_time,
                "last_msg_time": message_time,
                "message_count": self._count_messages_in_timerange(potential_start_time, message_time),
            }
            segments.append(new_segment)
            logger.info(f"[{self.stream_name}] 为用户 {person_id} 创建新消息段（超过10条消息间隔）: {new_segment}")

        self._save_cache()

    def _count_messages_in_timerange(self, start_time: float, end_time: float) -> int:
        """计算指定时间范围内的消息数量（包含边界）"""
        messages = get_raw_msg_by_timestamp_with_chat_inclusive(self.stream_id, start_time, end_time)
        return len(messages)

    def _count_messages_between(self, start_time: float, end_time: float) -> int:
        """计算两个时间点之间的消息数量（不包含边界），用于间隔检查"""
        return num_new_messages_since(self.stream_id, start_time, end_time)

    def _get_total_message_count(self, person_id: str) -> int:
        """获取用户所有消息段的总消息数量"""
        if person_id not in self.person_engaged_cache:
            return 0

        total_count = 0
        for segment in self.person_engaged_cache[person_id]:
            total_count += segment["message_count"]

        return total_count

    def _cleanup_old_segments(self) -> bool:
        """清理老旧的消息段

        Returns:
            bool: 是否执行了清理操作
        """
        if not SEGMENT_CLEANUP_CONFIG["enable_cleanup"]:
            return False

        current_time = time.time()

        # 检查是否需要执行清理（基于时间间隔）
        cleanup_interval_seconds = SEGMENT_CLEANUP_CONFIG["cleanup_interval_hours"] * 3600
        if current_time - self.last_cleanup_time < cleanup_interval_seconds:
            return False

        logger.info(f"[{self.stream_name}] 开始执行老消息段清理...")

        cleanup_stats = {
            "users_cleaned": 0,
            "segments_removed": 0,
            "total_segments_before": 0,
            "total_segments_after": 0,
        }

        max_age_seconds = SEGMENT_CLEANUP_CONFIG["max_segment_age_days"] * 24 * 3600
        max_segments_per_user = SEGMENT_CLEANUP_CONFIG["max_segments_per_user"]

        users_to_remove = []

        for person_id, segments in self.person_engaged_cache.items():
            cleanup_stats["total_segments_before"] += len(segments)
            original_segment_count = len(segments)

            # 1. 按时间清理：移除过期的消息段
            segments_after_age_cleanup = []
            for segment in segments:
                segment_age = current_time - segment["end_time"]
                if segment_age <= max_age_seconds:
                    segments_after_age_cleanup.append(segment)
                else:
                    cleanup_stats["segments_removed"] += 1
                    logger.debug(
                        f"[{self.stream_name}] 移除用户 {person_id} 的过期消息段: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(segment['start_time']))} - {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(segment['end_time']))}"
                    )

            # 2. 按数量清理：如果消息段数量仍然过多，保留最新的
            if len(segments_after_age_cleanup) > max_segments_per_user:
                # 按end_time排序，保留最新的
                segments_after_age_cleanup.sort(key=lambda x: x["end_time"], reverse=True)
                segments_removed_count = len(segments_after_age_cleanup) - max_segments_per_user
                cleanup_stats["segments_removed"] += segments_removed_count
                segments_after_age_cleanup = segments_after_age_cleanup[:max_segments_per_user]
                logger.debug(
                    f"[{self.stream_name}] 用户 {person_id} 消息段数量过多，移除 {segments_removed_count} 个最老的消息段"
                )

            # 使用清理后的消息段

            # 更新缓存
            if len(segments_after_age_cleanup) == 0:
                # 如果没有剩余消息段，标记用户为待移除
                users_to_remove.append(person_id)
            else:
                self.person_engaged_cache[person_id] = segments_after_age_cleanup
                cleanup_stats["total_segments_after"] += len(segments_after_age_cleanup)

            if original_segment_count != len(segments_after_age_cleanup):
                cleanup_stats["users_cleaned"] += 1

        # 移除没有消息段的用户
        for person_id in users_to_remove:
            del self.person_engaged_cache[person_id]
            logger.debug(f"[{self.stream_name}] 移除用户 {person_id}：没有剩余消息段")

        # 更新最后清理时间
        self.last_cleanup_time = current_time

        # 保存缓存
        if cleanup_stats["segments_removed"] > 0 or len(users_to_remove) > 0:
            self._save_cache()
            logger.info(
                f"[{self.stream_name}] 清理完成 - 影响用户: {cleanup_stats['users_cleaned']}, 移除消息段: {cleanup_stats['segments_removed']}, 移除用户: {len(users_to_remove)}"
            )
            logger.info(
                f"[{self.stream_name}] 消息段统计 - 清理前: {cleanup_stats['total_segments_before']}, 清理后: {cleanup_stats['total_segments_after']}"
            )
        else:
            logger.debug(f"[{self.stream_name}] 清理完成 - 无需清理任何内容")

        return cleanup_stats["segments_removed"] > 0 or len(users_to_remove) > 0

    def get_cache_status(self) -> str:
        """获取缓存状态信息，用于调试和监控"""
        if not self.person_engaged_cache:
            return f"[{self.stream_name}] 关系缓存为空"

        status_lines = [f"[{self.stream_name}] 关系缓存状态："]
        status_lines.append(
            f"最后处理消息时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.last_processed_message_time)) if self.last_processed_message_time > 0 else '未设置'}"
        )
        status_lines.append(
            f"最后清理时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.last_cleanup_time)) if self.last_cleanup_time > 0 else '未执行'}"
        )
        status_lines.append(f"总用户数：{len(self.person_engaged_cache)}")
        status_lines.append(
            f"清理配置：{'启用' if SEGMENT_CLEANUP_CONFIG['enable_cleanup'] else '禁用'} (最大保存{SEGMENT_CLEANUP_CONFIG['max_segment_age_days']}天, 每用户最多{SEGMENT_CLEANUP_CONFIG['max_segments_per_user']}段)"
        )
        status_lines.append("")

        for person_id, segments in self.person_engaged_cache.items():
            total_count = self._get_total_message_count(person_id)
            status_lines.append(f"用户 {person_id}:")
            status_lines.append(f"  总消息数：{total_count} ({total_count}/45)")
            status_lines.append(f"  消息段数：{len(segments)}")

            for i, segment in enumerate(segments):
                start_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(segment["start_time"]))
                end_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(segment["end_time"]))
                last_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(segment["last_msg_time"]))
                status_lines.append(
                    f"    段{i + 1}: {start_str} -> {end_str} (最后消息: {last_str}, 消息数: {segment['message_count']})"
                )
            status_lines.append("")

        return "\n".join(status_lines)

    def _update_user_message_segments(self, message: MessageRecv):
        """更新用户消息段信息"""
        time.time()
        user_id = message.message_info.user_info.user_id
        platform = message.message_info.platform
        msg_time = message.message_info.time

        # 跳过机器人自己的消息
        if user_id == global_config.bot.qq_account:
            return

        # 只处理新消息（避免重复处理）
        if msg_time <= self.last_processed_message_time:
            return

        person_id = PersonInfoManager.get_person_id(platform, user_id)
        self._update_message_segments(person_id, msg_time)

        # 更新最后处理时间
        self.last_processed_message_time = max(self.last_processed_message_time, msg_time)
        logger.debug(
            f"[{self.stream_name}] 更新用户 {person_id} 的消息段，消息时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(msg_time))}"
        )

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
        thinking_id = "tid" + str(thinking_time_point)
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

    async def _reply_interested_message(self) -> None:
        """
        后台任务方法，轮询当前实例关联chat的兴趣消息
        通常由start_monitoring_interest()启动
        """
        logger.debug(f"[{self.stream_name}] 兴趣监控任务开始")

        try:
            while True:
                # 第一层检查：立即检查取消和停用状态
                if self._disabled:
                    logger.info(f"[{self.stream_name}] 检测到停用标志，退出兴趣监控")
                    break

                # 检查当前任务是否已被取消
                current_task = asyncio.current_task()
                if current_task and current_task.cancelled():
                    logger.info(f"[{self.stream_name}] 当前任务已被取消，退出")
                    break

                try:
                    # 短暂等待，让出控制权
                    await asyncio.sleep(0.1)

                    # 第二层检查：睡眠后再次检查状态
                    if self._disabled:
                        logger.info(f"[{self.stream_name}] 睡眠后检测到停用标志，退出")
                        break

                    # 获取待处理消息
                    items_to_process = list(self.interest_dict.items())
                    if not items_to_process:
                        # 没有消息时继续下一轮循环
                        continue

                    # 第三层检查：在处理消息前最后检查一次
                    if self._disabled:
                        logger.info(f"[{self.stream_name}] 处理消息前检测到停用标志，退出")
                        break

                    # 使用异步上下文管理器处理消息
                    try:
                        async with global_prompt_manager.async_message_scope(
                            self.chat_stream.context.get_template_name()
                        ):
                            # 在上下文内部再次检查取消状态
                            if self._disabled:
                                logger.info(f"[{self.stream_name}] 在处理上下文中检测到停止信号，退出")
                                break

                            # 并行处理兴趣消息
                            async def process_single_message(msg_id, message, interest_value, is_mentioned):
                                """处理单个兴趣消息"""
                                try:
                                    # 在处理每个消息前检查停止状态
                                    if self._disabled:
                                        logger.debug(f"[{self.stream_name}] 处理消息时检测到停用，跳过消息 {msg_id}")
                                        return

                                    # 处理消息
                                    if time.time() - self.start_time > 300:
                                        self.adjust_reply_frequency(duration=300 / 60)
                                    else:
                                        self.adjust_reply_frequency(duration=(time.time() - self.start_time) / 60)

                                    await self.normal_response(
                                        message=message,
                                        is_mentioned=is_mentioned,
                                        interested_rate=interest_value * self.willing_amplifier,
                                    )
                                except asyncio.CancelledError:
                                    logger.debug(f"[{self.stream_name}] 处理消息 {msg_id} 时被取消")
                                    raise  # 重新抛出取消异常
                                except Exception as e:
                                    logger.error(f"[{self.stream_name}] 处理兴趣消息{msg_id}时出错: {e}")
                                    # 不打印完整traceback，避免日志污染
                                finally:
                                    # 无论如何都要清理消息
                                    self.interest_dict.pop(msg_id, None)

                            # 创建并行任务列表
                            coroutines = []
                            for msg_id, (message, interest_value, is_mentioned) in items_to_process:
                                coroutine = process_single_message(msg_id, message, interest_value, is_mentioned)
                                coroutines.append(coroutine)

                            # 并行执行所有任务，限制并发数量避免资源过度消耗
                            if coroutines:
                                # 使用信号量控制并发数，最多同时处理5个消息
                                semaphore = asyncio.Semaphore(5)

                                async def limited_process(coroutine, sem):
                                    async with sem:
                                        await coroutine

                                limited_tasks = [limited_process(coroutine, semaphore) for coroutine in coroutines]
                                await asyncio.gather(*limited_tasks, return_exceptions=True)

                    except asyncio.CancelledError:
                        logger.info(f"[{self.stream_name}] 处理上下文时任务被取消")
                        break
                    except Exception as e:
                        logger.error(f"[{self.stream_name}] 处理上下文时出错: {e}")
                        # 出错后短暂等待，避免快速重试
                        await asyncio.sleep(0.5)

                except asyncio.CancelledError:
                    logger.info(f"[{self.stream_name}] 主循环中任务被取消")
                    break
                except Exception as e:
                    logger.error(f"[{self.stream_name}] 主循环出错: {e}")
                    # 出错后等待一秒再继续
                    await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            logger.info(f"[{self.stream_name}] 兴趣监控任务被取消")
        except Exception as e:
            logger.error(f"[{self.stream_name}] 兴趣监控任务严重错误: {e}")
        finally:
            logger.debug(f"[{self.stream_name}] 兴趣监控任务结束")

    # 改为实例方法, 移除 chat 参数
    async def normal_response(self, message: MessageRecv, is_mentioned: bool, interested_rate: float) -> None:
        # 新增：如果已停用，直接返回
        if self._disabled:
            logger.info(f"[{self.stream_name}] 已停用，忽略 normal_response。")
            return

        # 新增：在auto模式下检查是否需要直接切换到focus模式
        if global_config.chat.chat_mode == "auto":
            should_switch = await self._check_should_switch_to_focus()
            if should_switch:
                logger.info(f"[{self.stream_name}] 检测到切换到focus聊天模式的条件，直接执行切换")
                if self.on_switch_to_focus_callback:
                    await self.on_switch_to_focus_callback()
                    return
                else:
                    logger.warning(f"[{self.stream_name}] 没有设置切换到focus聊天模式的回调函数，无法执行切换")

        # 执行定期清理
        self._cleanup_old_segments()

        # 更新消息段信息
        self._update_user_message_segments(message)

        # 检查是否有用户满足关系构建条件
        asyncio.create_task(self._check_relation_building_conditions())

        timing_results = {}
        reply_probability = (
            1.0 if is_mentioned and global_config.normal_chat.mentioned_bot_inevitable_reply else 0.0
        )  # 如果被提及，且开启了提及必回复，则基础概率为1，否则需要意愿判断

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

            thinking_id = await self._create_thinking_message(message)

            # 如果启用planner，预先修改可用actions（避免在并行任务中重复调用）
            available_actions = None
            if self.enable_planner:
                try:
                    await self.action_modifier.modify_actions_for_normal_chat(
                        self.chat_stream, self.recent_replies, message.processed_plain_text
                    )
                    available_actions = self.action_manager.get_using_actions_for_mode("normal")
                except Exception as e:
                    logger.warning(f"[{self.stream_name}] 获取available_actions失败: {e}")
                    available_actions = None

            # 定义并行执行的任务
            async def generate_normal_response():
                """生成普通回复"""
                try:
                    return await self.gpt.generate_response(
                        message=message,
                        thinking_id=thinking_id,
                        enable_planner=self.enable_planner,
                        available_actions=available_actions,
                    )
                except Exception as e:
                    logger.error(f"[{self.stream_name}] 回复生成出现错误：{str(e)} {traceback.format_exc()}")
                    return None

            async def plan_and_execute_actions():
                """规划和执行额外动作"""
                if not self.enable_planner:
                    logger.debug(f"[{self.stream_name}] Planner未启用，跳过动作规划")
                    return None

                try:
                    # 获取发送者名称（动作修改已在并行执行前完成）
                    sender_name = self._get_sender_name(message)

                    no_action = {
                        "action_result": {
                            "action_type": "no_action",
                            "action_data": {},
                            "reasoning": "规划器初始化默认",
                            "is_parallel": True,
                        },
                        "chat_context": "",
                        "action_prompt": "",
                    }

                    # 检查是否应该跳过规划
                    if self.action_modifier.should_skip_planning():
                        logger.debug(f"[{self.stream_name}] 没有可用动作，跳过规划")
                        self.action_type = "no_action"
                        return no_action

                    # 执行规划
                    plan_result = await self.planner.plan(message, sender_name)
                    action_type = plan_result["action_result"]["action_type"]
                    action_data = plan_result["action_result"]["action_data"]
                    reasoning = plan_result["action_result"]["reasoning"]
                    is_parallel = plan_result["action_result"].get("is_parallel", False)

                    logger.info(
                        f"[{self.stream_name}] Planner决策: {action_type}, 理由: {reasoning}, 并行执行: {is_parallel}"
                    )
                    self.action_type = action_type  # 更新实例属性
                    self.is_parallel_action = is_parallel  # 新增：保存并行执行标志

                    # 如果规划器决定不执行任何动作
                    if action_type == "no_action":
                        logger.debug(f"[{self.stream_name}] Planner决定不执行任何额外动作")
                        return no_action

                    # 执行额外的动作（不影响回复生成）
                    action_result = await self._execute_action(action_type, action_data, message, thinking_id)
                    if action_result is not None:
                        logger.info(f"[{self.stream_name}] 额外动作 {action_type} 执行完成")
                    else:
                        logger.warning(f"[{self.stream_name}] 额外动作 {action_type} 执行失败")

                    return {
                        "action_type": action_type,
                        "action_data": action_data,
                        "reasoning": reasoning,
                        "is_parallel": is_parallel,
                    }

                except Exception as e:
                    logger.error(f"[{self.stream_name}] Planner执行失败: {e}")
                    return no_action

            # 并行执行回复生成和动作规划
            self.action_type = None  # 初始化动作类型
            self.is_parallel_action = False  # 初始化并行动作标志
            with Timer("并行生成回复和规划", timing_results):
                response_set, plan_result = await asyncio.gather(
                    generate_normal_response(), plan_and_execute_actions(), return_exceptions=True
                )

            # 处理生成回复的结果
            if isinstance(response_set, Exception):
                logger.error(f"[{self.stream_name}] 回复生成异常: {response_set}")
                response_set = None

            # 处理规划结果（可选，不影响回复）
            if isinstance(plan_result, Exception):
                logger.error(f"[{self.stream_name}] 动作规划异常: {plan_result}")
            elif plan_result:
                logger.debug(f"[{self.stream_name}] 额外动作处理完成: {self.action_type}")

            if not response_set or (
                self.enable_planner and self.action_type not in ["no_action"] and not self.is_parallel_action
            ):
                if not response_set:
                    logger.info(f"[{self.stream_name}] 模型未生成回复内容")
                elif self.enable_planner and self.action_type not in ["no_action"] and not self.is_parallel_action:
                    logger.info(f"[{self.stream_name}] 模型选择其他动作（非并行动作）")
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
                # 消息段已在接收消息时更新，这里不需要额外处理

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
        """启动聊天任务。"""
        logger.debug(f"[{self.stream_name}] 开始启动聊天任务")

        # 重置停用标志
        self._disabled = False

        # 检查是否已有运行中的任务
        if self._chat_task and not self._chat_task.done():
            logger.info(f"[{self.stream_name}] 聊天轮询任务已在运行中。")
            return

        # 清理可能存在的已完成任务引用
        if self._chat_task and self._chat_task.done():
            self._chat_task = None

        try:
            logger.debug(f"[{self.stream_name}] 创建新的聊天轮询任务")
            polling_task = asyncio.create_task(self._reply_interested_message())

            # 设置回调
            polling_task.add_done_callback(lambda t: self._handle_task_completion(t))

            # 保存任务引用
            self._chat_task = polling_task

            logger.debug(f"[{self.stream_name}] 聊天任务启动完成")

        except Exception as e:
            logger.error(f"[{self.stream_name}] 启动聊天任务失败: {e}")
            self._chat_task = None
            raise

    def _handle_task_completion(self, task: asyncio.Task):
        """任务完成回调处理"""
        try:
            # 简化回调逻辑，避免复杂的异常处理
            logger.debug(f"[{self.stream_name}] 任务完成回调被调用")

            # 检查是否是我们管理的任务
            if task is not self._chat_task:
                # 如果已经不是当前任务（可能在stop_chat中已被清空），直接返回
                logger.debug(f"[{self.stream_name}] 回调的任务不是当前管理的任务")
                return

            # 清理任务引用
            self._chat_task = None
            logger.debug(f"[{self.stream_name}] 任务引用已清理")

            # 简单记录任务状态，不进行复杂处理
            if task.cancelled():
                logger.debug(f"[{self.stream_name}] 任务已取消")
            elif task.done():
                try:
                    # 尝试获取异常，但不抛出
                    exc = task.exception()
                    if exc:
                        logger.error(f"[{self.stream_name}] 任务异常: {type(exc).__name__}: {exc}")
                    else:
                        logger.debug(f"[{self.stream_name}] 任务正常完成")
                except Exception as e:
                    # 获取异常时也可能出错，静默处理
                    logger.debug(f"[{self.stream_name}] 获取任务异常时出错: {e}")

        except Exception as e:
            # 回调函数中的任何异常都要捕获，避免影响系统
            logger.error(f"[{self.stream_name}] 任务完成回调处理出错: {e}")
            # 确保任务引用被清理
            self._chat_task = None

    # 改为实例方法, 移除 stream_id 参数
    async def stop_chat(self):
        """停止当前实例的兴趣监控任务。"""
        logger.debug(f"[{self.stream_name}] 开始停止聊天任务")

        # 立即设置停用标志，防止新任务启动
        self._disabled = True

        # 如果没有运行中的任务，直接返回
        if not self._chat_task or self._chat_task.done():
            logger.debug(f"[{self.stream_name}] 没有运行中的任务，直接完成停止")
            self._chat_task = None
            return

        # 保存任务引用并立即清空，避免回调中的循环引用
        task_to_cancel = self._chat_task
        self._chat_task = None

        logger.debug(f"[{self.stream_name}] 取消聊天任务")

        # 尝试优雅取消任务
        task_to_cancel.cancel()

        # 不等待任务完成，让它自然结束
        # 这样可以避免等待过程中的潜在递归问题

        # 异步清理思考消息，不阻塞当前流程
        asyncio.create_task(self._cleanup_thinking_messages_async())

        logger.debug(f"[{self.stream_name}] 聊天任务停止完成")

    async def _cleanup_thinking_messages_async(self):
        """异步清理思考消息，避免阻塞主流程"""
        try:
            # 添加短暂延迟，让任务有时间响应取消
            await asyncio.sleep(0.1)

            container = await message_manager.get_container(self.stream_id)
            if container:
                # 查找并移除所有 MessageThinking 类型的消息
                thinking_messages = [msg for msg in container.messages[:] if isinstance(msg, MessageThinking)]
                if thinking_messages:
                    for msg in thinking_messages:
                        container.messages.remove(msg)
                    logger.info(f"[{self.stream_name}] 清理了 {len(thinking_messages)} 条未处理的思考消息。")
        except Exception as e:
            logger.error(f"[{self.stream_name}] 异步清理思考消息时出错: {e}")
            # 不打印完整栈跟踪，避免日志污染

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

        differ = global_config.chat.talk_frequency - (bot_reply_count / duration)

        # 如果回复频率低于0.5，增加回复概率
        if differ > 0.1:
            mapped = 1 + (differ - 0.1) * 4 / 0.9
            mapped = max(1, min(5, mapped))
            logger.debug(
                f"[{self.stream_name}] 回复频率低于{global_config.chat.talk_frequency}，增加回复概率，differ={differ:.3f}，映射值={mapped:.2f}"
            )
            self.willing_amplifier += mapped * 0.1  # 你可以根据实际需要调整系数
        elif differ < -0.1:
            mapped = 1 - (differ + 0.1) * 4 / 0.9
            mapped = max(1, min(5, mapped))
            logger.debug(
                f"[{self.stream_name}] 回复频率高于{global_config.chat.talk_frequency}，减少回复概率，differ={differ:.3f}，映射值={mapped:.2f}"
            )
            self.willing_amplifier -= mapped * 0.1

        if self.willing_amplifier > 5:
            self.willing_amplifier = 5
        elif self.willing_amplifier < 0.1:
            self.willing_amplifier = 0.1

    def _get_sender_name(self, message: MessageRecv) -> str:
        """获取发送者名称，用于planner"""
        if message.chat_stream.user_info:
            user_info = message.chat_stream.user_info
            if user_info.user_cardname and user_info.user_nickname:
                return f"[{user_info.user_nickname}][群昵称：{user_info.user_cardname}]"
            elif user_info.user_nickname:
                return f"[{user_info.user_nickname}]"
            else:
                return f"用户({user_info.user_id})"
        return "某人"

    async def _execute_action(
        self, action_type: str, action_data: dict, message: MessageRecv, thinking_id: str
    ) -> Optional[bool]:
        """执行具体的动作，只返回执行成功与否"""
        try:
            # 创建动作处理器实例
            action_handler = self.action_manager.create_action(
                action_name=action_type,
                action_data=action_data,
                reasoning=action_data.get("reasoning", ""),
                cycle_timers={},  # normal_chat使用空的cycle_timers
                thinking_id=thinking_id,
                chat_stream=self.chat_stream,
                log_prefix=self.stream_name,
                shutting_down=self._disabled,
            )

            if action_handler:
                # 执行动作
                result = await action_handler.handle_action()
                success = False

                if result and isinstance(result, tuple) and len(result) >= 2:
                    # handle_action返回 (success: bool, message: str)
                    success = result[0]
                elif result:
                    # 如果返回了其他结果，假设成功
                    success = True

                return success

        except Exception as e:
            logger.error(f"[{self.stream_name}] 执行动作 {action_type} 失败: {e}")

        return False

    def set_planner_enabled(self, enabled: bool):
        """设置是否启用planner"""
        self.enable_planner = enabled
        logger.info(f"[{self.stream_name}] Planner {'启用' if enabled else '禁用'}")

    def get_action_manager(self) -> ActionManager:
        """获取动作管理器实例"""
        return self.action_manager

    async def _check_relation_building_conditions(self):
        """检查person_engaged_cache中是否有满足关系构建条件的用户"""
        users_to_build_relationship = []

        for person_id, segments in list(self.person_engaged_cache.items()):
            total_message_count = self._get_total_message_count(person_id)
            if total_message_count >= 45:
                users_to_build_relationship.append(person_id)
                logger.info(
                    f"[{self.stream_name}] 用户 {person_id} 满足关系构建条件，总消息数：{total_message_count}，消息段数：{len(segments)}"
                )
            elif total_message_count > 0:
                # 记录进度信息
                logger.debug(
                    f"[{self.stream_name}] 用户 {person_id} 进度：{total_message_count}/45 条消息，{len(segments)} 个消息段"
                )

        # 为满足条件的用户构建关系
        for person_id in users_to_build_relationship:
            segments = self.person_engaged_cache[person_id]
            # 异步执行关系构建
            asyncio.create_task(self._build_relation_for_person_segments(person_id, segments))
            # 移除已处理的用户缓存
            del self.person_engaged_cache[person_id]
            self._save_cache()
            logger.info(f"[{self.stream_name}] 用户 {person_id} 关系构建已启动，缓存已清理")

    async def _build_relation_for_person_segments(self, person_id: str, segments: List[Dict[str, any]]):
        """基于消息段更新用户印象，统一使用focus chat的构建方式"""
        if not segments:
            return

        logger.debug(f"[{self.stream_name}] 开始为 {person_id} 基于 {len(segments)} 个消息段更新印象")
        try:
            processed_messages = []

            for i, segment in enumerate(segments):
                start_time = segment["start_time"]
                end_time = segment["end_time"]
                segment["message_count"]
                start_date = time.strftime("%Y-%m-%d %H:%M", time.localtime(start_time))

                # 获取该段的消息（包含边界）
                segment_messages = get_raw_msg_by_timestamp_with_chat_inclusive(self.stream_id, start_time, end_time)
                logger.debug(
                    f"[{self.stream_name}] 消息段 {i + 1}: {start_date} - {time.strftime('%Y-%m-%d %H:%M', time.localtime(end_time))}, 消息数: {len(segment_messages)}"
                )

                if segment_messages:
                    # 如果不是第一个消息段，在消息列表前添加间隔标识
                    if i > 0:
                        # 创建一个特殊的间隔消息
                        gap_message = {
                            "time": start_time - 0.1,  # 稍微早于段开始时间
                            "user_id": "system",
                            "user_platform": "system",
                            "user_nickname": "系统",
                            "user_cardname": "",
                            "display_message": f"...（中间省略一些消息）{start_date} 之后的消息如下...",
                            "is_action_record": True,
                            "chat_info_platform": segment_messages[0].get("chat_info_platform", ""),
                            "chat_id": self.stream_id,
                        }
                        processed_messages.append(gap_message)

                    # 添加该段的所有消息
                    processed_messages.extend(segment_messages)

            if processed_messages:
                # 按时间排序所有消息（包括间隔标识）
                processed_messages.sort(key=lambda x: x["time"])

                logger.debug(
                    f"[{self.stream_name}] 为 {person_id} 获取到总共 {len(processed_messages)} 条消息（包含间隔标识）用于印象更新"
                )
                relationship_manager = get_relationship_manager()

                # 调用统一的更新方法
                await relationship_manager.update_person_impression(
                    person_id=person_id, timestamp=time.time(), bot_engaged_messages=processed_messages
                )
            else:
                logger.debug(f"[{self.stream_name}] 没有找到 {person_id} 的消息段对应的消息，不更新印象")

        except Exception as e:
            logger.error(f"[{self.stream_name}] 为 {person_id} 更新印象时发生错误: {e}")
            logger.error(traceback.format_exc())

    async def _check_should_switch_to_focus(self) -> bool:
        """
        检查是否满足切换到focus模式的条件

        Returns:
            bool: 是否应该切换到focus模式
        """
        # 检查思考消息堆积情况
        container = await message_manager.get_container(self.stream_id)
        if container:
            thinking_count = sum(1 for msg in container.messages if isinstance(msg, MessageThinking))
            if thinking_count >= 4 * global_config.chat.auto_focus_threshold:  # 如果堆积超过阈值条思考消息
                logger.debug(f"[{self.stream_name}] 检测到思考消息堆积({thinking_count}条)，切换到focus模式")
                return True

        if not self.recent_replies:
            return False

        current_time = time.time()
        time_threshold = 120 / global_config.chat.auto_focus_threshold
        reply_threshold = 6 * global_config.chat.auto_focus_threshold

        one_minute_ago = current_time - time_threshold

        # 统计指定时间内的回复数量
        recent_reply_count = sum(1 for reply in self.recent_replies if reply["time"] > one_minute_ago)

        should_switch = recent_reply_count > reply_threshold
        if should_switch:
            logger.debug(
                f"[{self.stream_name}] 检测到{time_threshold:.0f}秒内回复数量({recent_reply_count})大于{reply_threshold}，满足切换到focus模式条件"
            )

        return should_switch
