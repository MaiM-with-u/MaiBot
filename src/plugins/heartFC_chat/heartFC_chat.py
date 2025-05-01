import asyncio
import time
import traceback
import random  # <--- 添加导入
import json  # <--- 确保导入 json
from typing import List, Optional, Dict, Any, Deque, Callable, Coroutine
from collections import deque
from src.plugins.chat.message import MessageRecv, BaseMessageInfo, MessageThinking, MessageSending
from src.plugins.chat.message import Seg  # Local import needed after move
from src.plugins.chat.chat_stream import ChatStream
from src.plugins.chat.message import UserInfo
from src.plugins.chat.chat_stream import chat_manager
from src.common.logger_manager import get_logger
from src.plugins.models.utils_model import LLMRequest
from src.config.config import global_config
from src.plugins.chat.utils_image import image_path_to_base64  # Local import needed after move
from src.plugins.utils.timer_calculator import Timer  # <--- Import Timer
from src.plugins.emoji_system.emoji_manager import emoji_manager
from src.heart_flow.sub_mind import SubMind
from src.heart_flow.observation import Observation
from src.plugins.heartFC_chat.heartflow_prompt_builder import global_prompt_manager, prompt_builder
import contextlib
from src.plugins.utils.chat_message_builder import num_new_messages_since
from src.plugins.heartFC_chat.heartFC_Cycleinfo import CycleInfo
from .heartFC_sender import HeartFCSender
from src.plugins.chat.utils import process_llm_response
from src.plugins.respon_info_catcher.info_catcher import info_catcher_manager
from src.plugins.moods.moods import MoodManager
from src.individuality.individuality import Individuality


WAITING_TIME_THRESHOLD = 300  # 等待新消息时间阈值，单位秒

EMOJI_SEND_PRO = 0.3  # 设置一个概率，比如 30% 才真的发

CONSECUTIVE_NO_REPLY_THRESHOLD = 3  # 连续不回复的阈值


logger = get_logger("hfc")  # Logger Name Changed


# 默认动作定义
DEFAULT_ACTIONS = {"no_reply": "不回复", "text_reply": "文本回复, 可选附带表情", "emoji_reply": "仅表情回复"}


class ActionManager:
    """动作管理器：控制每次决策可以使用的动作"""

    def __init__(self):
        # 初始化为默认动作集
        self._available_actions: Dict[str, str] = DEFAULT_ACTIONS.copy()
        self._original_actions_backup: Optional[Dict[str, str]] = None  # 用于临时移除时的备份

    def get_available_actions(self) -> Dict[str, str]:
        """获取当前可用的动作集"""
        return self._available_actions.copy()  # 返回副本以防外部修改

    def add_action(self, action_name: str, description: str) -> bool:
        """
        添加新的动作

        参数:
            action_name: 动作名称
            description: 动作描述

        返回:
            bool: 是否添加成功
        """
        if action_name in self._available_actions:
            return False
        self._available_actions[action_name] = description
        return True

    def remove_action(self, action_name: str) -> bool:
        """
        移除指定动作

        参数:
            action_name: 动作名称

        返回:
            bool: 是否移除成功
        """
        if action_name not in self._available_actions:
            return False
        del self._available_actions[action_name]
        return True

    def temporarily_remove_actions(self, actions_to_remove: List[str]):
        """
        临时移除指定的动作，备份原始动作集。
        如果已经有备份，则不重复备份。
        """
        if self._original_actions_backup is None:
            self._original_actions_backup = self._available_actions.copy()

        actions_actually_removed = []
        for action_name in actions_to_remove:
            if action_name in self._available_actions:
                del self._available_actions[action_name]
                actions_actually_removed.append(action_name)
        # logger.debug(f"临时移除了动作: {actions_actually_removed}") # 可选日志

    def restore_actions(self):
        """
        恢复之前备份的原始动作集。
        """
        if self._original_actions_backup is not None:
            self._available_actions = self._original_actions_backup.copy()
            self._original_actions_backup = None
            # logger.debug("恢复了原始动作集") # 可选日志

    def clear_actions(self):
        """清空所有动作"""
        self._available_actions.clear()

    def reset_to_default(self):
        """重置为默认动作集"""
        self._available_actions = DEFAULT_ACTIONS.copy()


# 在文件开头添加自定义异常类
class HeartFCError(Exception):
    """麦麦聊天系统基础异常类"""

    pass


class PlannerError(HeartFCError):
    """规划器异常"""

    pass


class ReplierError(HeartFCError):
    """回复器异常"""

    pass


class SenderError(HeartFCError):
    """发送器异常"""

    pass


async def _handle_cycle_delay(action_taken_this_cycle: bool, cycle_start_time: float, log_prefix: str):
    """处理循环延迟"""
    cycle_duration = time.monotonic() - cycle_start_time

    try:
        sleep_duration = 0.0
        if not action_taken_this_cycle and cycle_duration < 1:
            sleep_duration = 1 - cycle_duration
        elif cycle_duration < 0.2:
            sleep_duration = 0.2

        if sleep_duration > 0:
            await asyncio.sleep(sleep_duration)

    except asyncio.CancelledError:
        logger.info(f"{log_prefix} Sleep interrupted, loop likely cancelling.")
        raise


class HeartFChatting:
    """
    管理一个连续的Plan-Replier-Sender循环
    用于在特定聊天流中生成回复。
    其生命周期现在由其关联的 SubHeartflow 的 FOCUSED 状态控制。
    """

    def __init__(
        self,
        chat_id: str,
        sub_mind: SubMind,
        observations: list[Observation],
        on_consecutive_no_reply_callback: Callable[[], Coroutine[None, None, None]],
    ):
        """
        HeartFChatting 初始化函数

        参数:
            chat_id: 聊天流唯一标识符(如stream_id)
            sub_mind: 关联的子思维
            observations: 关联的观察列表
            on_consecutive_no_reply_callback: 连续不回复达到阈值时调用的异步回调函数
        """
        # 基础属性
        self.stream_id: str = chat_id  # 聊天流ID
        self.chat_stream: Optional[ChatStream] = None  # 关联的聊天流
        self.sub_mind: SubMind = sub_mind  # 关联的子思维
        self.observations: List[Observation] = observations  # 关联的观察列表，用于监控聊天流状态
        self.on_consecutive_no_reply_callback = on_consecutive_no_reply_callback

        # 日志前缀
        self.log_prefix: str = f"[{chat_manager.get_stream_name(chat_id) or chat_id}]"

        # 动作管理器
        self.action_manager = ActionManager()

        # 初始化状态控制
        self._initialized = False
        self._processing_lock = asyncio.Lock()

        # --- 移除 gpt_instance, 直接初始化 LLM 模型 ---
        # self.gpt_instance = HeartFCGenerator() # <-- 移除
        self.model_normal = LLMRequest(  # <-- 新增 LLM 初始化
            model=global_config.llm_normal,
            temperature=global_config.llm_normal["temp"],
            max_tokens=256,
            request_type="response_heartflow",
        )
        self.heart_fc_sender = HeartFCSender()

        # LLM规划器配置
        self.planner_llm = LLMRequest(
            model=global_config.llm_plan,
            max_tokens=1000,
            request_type="action_planning",  # 用于动作规划
        )

        # 循环控制内部状态
        self._loop_active: bool = False  # 循环是否正在运行
        self._loop_task: Optional[asyncio.Task] = None  # 主循环任务

        # 添加循环信息管理相关的属性
        self._cycle_counter = 0
        self._cycle_history: Deque[CycleInfo] = deque(maxlen=10)  # 保留最近10个循环的信息
        self._current_cycle: Optional[CycleInfo] = None
        self._lian_xu_bu_hui_fu_ci_shu: int = 0  # <--- 新增：连续不回复计数器
        self._shutting_down: bool = False  # <--- 新增：关闭标志位
        self._lian_xu_deng_dai_shi_jian: float = 0.0  # <--- 新增：累计等待时间

    async def _initialize(self) -> bool:
        """
        懒初始化以使用提供的标识符解析chat_stream。
        确保实例已准备好处理触发器。
        """
        if self._initialized:
            return True

        self.chat_stream = chat_manager.get_stream(self.stream_id)
        if not self.chat_stream:
            logger.error(f"{self.log_prefix} 获取ChatStream失败。")
            return False

        # 更新日志前缀（以防流名称发生变化）
        self.log_prefix = f"[{chat_manager.get_stream_name(self.stream_id) or self.stream_id}]"

        self._initialized = True
        logger.debug(f"{self.log_prefix}麦麦感觉到了，可以开始认真水群 ")
        return True

    async def start(self):
        """
        启动 HeartFChatting 的主循环。
        注意：调用此方法前必须确保已经成功初始化。
        """
        logger.info(f"{self.log_prefix} 开始认真水群(HFC)...")
        await self._start_loop_if_needed()

    async def _start_loop_if_needed(self):
        """检查是否需要启动主循环，如果未激活则启动。"""
        # 如果循环已经激活，直接返回
        if self._loop_active:
            return

        # 标记为活动状态，防止重复启动
        self._loop_active = True

        # 检查是否已有任务在运行（理论上不应该，因为 _loop_active=False）
        if self._loop_task and not self._loop_task.done():
            logger.warning(f"{self.log_prefix} 发现之前的循环任务仍在运行（不符合预期）。取消旧任务。")
            self._loop_task.cancel()
            try:
                # 等待旧任务确实被取消
                await asyncio.wait_for(self._loop_task, timeout=0.5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass  # 忽略取消或超时错误
            self._loop_task = None  # 清理旧任务引用

        logger.debug(f"{self.log_prefix} 启动认真水群(HFC)主循环...")
        # 创建新的循环任务
        self._loop_task = asyncio.create_task(self._hfc_loop())
        # 添加完成回调
        self._loop_task.add_done_callback(self._handle_loop_completion)

    def _handle_loop_completion(self, task: asyncio.Task):
        """当 _hfc_loop 任务完成时执行的回调。"""
        try:
            exception = task.exception()
            if exception:
                logger.error(f"{self.log_prefix} HeartFChatting: 麦麦脱离了聊天(异常): {exception}")
                logger.error(traceback.format_exc())  # Log full traceback for exceptions
            else:
                # Loop completing normally now means it was cancelled/shutdown externally
                logger.info(f"{self.log_prefix} HeartFChatting: 麦麦脱离了聊天 (外部停止)")
        except asyncio.CancelledError:
            logger.info(f"{self.log_prefix} HeartFChatting: 麦麦脱离了聊天(任务取消)")
        finally:
            self._loop_active = False
            self._loop_task = None
            if self._processing_lock.locked():
                logger.warning(f"{self.log_prefix} HeartFChatting: 处理锁在循环结束时仍被锁定，强制释放。")
                self._processing_lock.release()

    async def _hfc_loop(self):
        """主循环，持续进行计划并可能回复消息，直到被外部取消。"""
        try:
            while True:  # 主循环
                logger.debug(f"{self.log_prefix} 开始第{self._cycle_counter}次循环")
                # --- 在循环开始处检查关闭标志 ---
                if self._shutting_down:
                    logger.info(f"{self.log_prefix} 检测到关闭标志，退出 HFC 循环。")
                    break
                # --------------------------------

                # 创建新的循环信息
                self._cycle_counter += 1
                self._current_cycle = CycleInfo(self._cycle_counter)

                # 初始化周期状态
                cycle_timers = {}
                loop_cycle_start_time = time.monotonic()

                # 执行规划和处理阶段
                async with self._get_cycle_context() as acquired_lock:
                    if not acquired_lock:
                        # 如果未能获取锁（理论上不太可能，除非 shutdown 过程中释放了但又被抢了？）
                        # 或者也可以在这里再次检查 self._shutting_down
                        if self._shutting_down:
                            break  # 再次检查，确保退出
                        logger.warning(f"{self.log_prefix} 未能获取循环处理锁，跳过本次循环。")
                        await asyncio.sleep(0.1)  # 短暂等待避免空转
                        continue

                    # 记录规划开始时间点
                    planner_start_db_time = time.time()

                    # 主循环：思考->决策->执行
                    action_taken, thinking_id = await self._think_plan_execute_loop(cycle_timers, planner_start_db_time)

                    # 更新循环信息
                    self._current_cycle.set_thinking_id(thinking_id)
                    self._current_cycle.timers = cycle_timers

                    # 防止循环过快消耗资源
                    await _handle_cycle_delay(action_taken, loop_cycle_start_time, self.log_prefix)

                # 完成当前循环并保存历史
                self._current_cycle.complete_cycle()
                self._cycle_history.append(self._current_cycle)

                # 记录循环信息和计时器结果
                timer_strings = []
                for name, elapsed in cycle_timers.items():
                    formatted_time = f"{elapsed * 1000:.2f}毫秒" if elapsed < 1 else f"{elapsed:.2f}秒"
                    timer_strings.append(f"{name}: {formatted_time}")

                logger.debug(
                    f"{self.log_prefix}  第 #{self._current_cycle.cycle_id}次思考完成,"
                    f"耗时: {self._current_cycle.end_time - self._current_cycle.start_time:.2f}秒, "
                    f"动作: {self._current_cycle.action_type}"
                    + (f"\n计时器详情: {'; '.join(timer_strings)}" if timer_strings else "")
                )

        except asyncio.CancelledError:
            # 设置了关闭标志位后被取消是正常流程
            if not self._shutting_down:
                logger.warning(f"{self.log_prefix} HeartFChatting: 麦麦的认真水群(HFC)循环意外被取消")
            else:
                logger.info(f"{self.log_prefix} HeartFChatting: 麦麦的认真水群(HFC)循环已取消 (正常关闭)")
        except Exception as e:
            logger.error(f"{self.log_prefix} HeartFChatting: 意外错误: {e}")
            logger.error(traceback.format_exc())

    @contextlib.asynccontextmanager
    async def _get_cycle_context(self):
        """
        循环周期的上下文管理器

        用于确保资源的正确获取和释放：
        1. 获取处理锁
        2. 执行操作
        3. 释放锁
        """
        acquired = False
        try:
            await self._processing_lock.acquire()
            acquired = True
            yield acquired
        finally:
            if acquired and self._processing_lock.locked():
                self._processing_lock.release()

    async def _check_new_messages(self, start_time: float) -> bool:
        """
        检查从指定时间点后是否有新消息

        参数:
            start_time: 开始检查的时间点

        返回:
            bool: 是否有新消息
        """
        try:
            new_msg_count = num_new_messages_since(self.stream_id, start_time)
            if new_msg_count > 0:
                logger.info(f"{self.log_prefix} 检测到{new_msg_count}条新消息")
                return True
            return False
        except Exception as e:
            logger.error(f"{self.log_prefix} 检查新消息时出错: {e}")
            return False

    async def _think_plan_execute_loop(self, cycle_timers: dict, planner_start_db_time: float) -> tuple[bool, str]:
        """执行规划阶段"""
        try:
            # think:思考
            current_mind = await self._get_submind_thinking(cycle_timers)
            # 记录子思维思考内容
            if self._current_cycle:
                self._current_cycle.set_response_info(sub_mind_thinking=current_mind)

            # plan:决策
            with Timer("决策", cycle_timers):
                planner_result = await self._planner(current_mind, cycle_timers)

            # 效果不太好，还没处理replan导致观察时间点改变的问题

            # action = planner_result.get("action", "error")
            # reasoning = planner_result.get("reasoning", "未提供理由")

            # self._current_cycle.set_action_info(action, reasoning, False)

            # 在获取规划结果后检查新消息

            # if await self._check_new_messages(planner_start_db_time):
            #     if random.random() < 0.2:
            #         logger.info(f"{self.log_prefix} 看到了新消息，麦麦决定重新观察和规划...")
            #         # 重新规划
            #         with Timer("重新决策", cycle_timers):
            #             self._current_cycle.replanned = True
            #             planner_result = await self._planner(current_mind, cycle_timers, is_re_planned=True)
            #         logger.info(f"{self.log_prefix} 重新规划完成.")

            # 解析规划结果
            action = planner_result.get("action", "error")
            reasoning = planner_result.get("reasoning", "未提供理由")
            # 更新循环信息
            self._current_cycle.set_action_info(action, reasoning, True)

            # 处理LLM错误
            if planner_result.get("llm_error"):
                logger.error(f"{self.log_prefix} LLM失败: {reasoning}")
                return False, ""

            # execute:执行

            # 在此处添加日志记录
            if action == "text_reply":
                action_str = "回复"
            elif action == "emoji_reply":
                action_str = "回复表情"
            else:
                action_str = "不回复"

            logger.info(f"{self.log_prefix} 麦麦决定'{action_str}', 原因'{reasoning}'")

            return await self._handle_action(
                action, reasoning, planner_result.get("emoji_query", ""), cycle_timers, planner_start_db_time
            )

        except PlannerError as e:
            logger.error(f"{self.log_prefix} 规划错误: {e}")
            # 更新循环信息
            self._current_cycle.set_action_info("error", str(e), False)
            return False, ""

    async def _handle_action(
        self, action: str, reasoning: str, emoji_query: str, cycle_timers: dict, planner_start_db_time: float
    ) -> tuple[bool, str]:
        """
        处理规划动作

        参数:
            action: 动作类型
            reasoning: 决策理由
            emoji_query: 表情查询
            cycle_timers: 计时器字典
            planner_start_db_time: 规划开始时间

        返回:
            tuple[bool, str]: (是否执行了动作, 思考消息ID)
        """
        action_handlers = {
            "text_reply": self._handle_text_reply,
            "emoji_reply": self._handle_emoji_reply,
            "no_reply": self._handle_no_reply,
        }

        handler = action_handlers.get(action)
        if not handler:
            logger.warning(f"{self.log_prefix} 未知动作: {action}, 原因: {reasoning}")
            return False, ""

        try:
            if action == "text_reply":
                return await handler(reasoning, emoji_query, cycle_timers)
            elif action == "emoji_reply":
                return await handler(reasoning, emoji_query), ""
            else:  # no_reply
                return await handler(reasoning, planner_start_db_time, cycle_timers), ""
        except HeartFCError as e:
            logger.error(f"{self.log_prefix} 处理{action}时出错: {e}")
            # 出错时也重置计数器
            self._lian_xu_bu_hui_fu_ci_shu = 0
            self._lian_xu_deng_dai_shi_jian = 0.0  # 重置累计等待时间
            return False, ""

    async def _handle_text_reply(self, reasoning: str, emoji_query: str, cycle_timers: dict) -> tuple[bool, str]:
        """
        处理文本回复

        工作流程：
        1. 获取锚点消息
        2. 创建思考消息
        3. 生成回复
        4. 发送消息

        参数:
            reasoning: 回复原因
            emoji_query: 表情查询
            cycle_timers: 计时器字典

        返回:
            tuple[bool, str]: (是否回复成功, 思考消息ID)
        """
        # 重置连续不回复计数器
        self._lian_xu_bu_hui_fu_ci_shu = 0
        self._lian_xu_deng_dai_shi_jian = 0.0  # 重置累计等待时间

        # 获取锚点消息
        anchor_message = await self._get_anchor_message()
        if not anchor_message:
            raise PlannerError("无法获取锚点消息")

        # 创建思考消息
        thinking_id = await self._create_thinking_message(anchor_message)
        if not thinking_id:
            raise PlannerError("无法创建思考消息")

        try:
            # 生成回复
            with Timer("生成回复", cycle_timers):
                reply = await self._replier_work(
                    anchor_message=anchor_message,
                    thinking_id=thinking_id,
                    reason=reasoning,
                )

            if not reply:
                raise ReplierError("回复生成失败")

            # 发送消息

            with Timer("发送消息", cycle_timers):
                await self._sender(
                    thinking_id=thinking_id,
                    anchor_message=anchor_message,
                    response_set=reply,
                    send_emoji=emoji_query,
                )

            return True, thinking_id

        except (ReplierError, SenderError) as e:
            logger.error(f"{self.log_prefix} 回复失败: {e}")
            return True, thinking_id  # 仍然返回thinking_id以便跟踪

    async def _handle_emoji_reply(self, reasoning: str, emoji_query: str) -> bool:
        """
        处理表情回复

        工作流程：
        1. 获取锚点消息
        2. 发送表情

        参数:
            reasoning: 回复原因
            emoji_query: 表情查询

        返回:
            bool: 是否发送成功
        """
        logger.info(f"{self.log_prefix} 决定回复表情({emoji_query}): {reasoning}")
        self._lian_xu_deng_dai_shi_jian = 0.0  # 重置累计等待时间（即使不计数也保持一致性）

        try:
            anchor = await self._get_anchor_message()
            if not anchor:
                raise PlannerError("无法获取锚点消息")

            await self._handle_emoji(anchor, [], emoji_query)
            return True

        except Exception as e:
            logger.error(f"{self.log_prefix} 表情发送失败: {e}")
            return False

    async def _handle_no_reply(self, reasoning: str, planner_start_db_time: float, cycle_timers: dict) -> bool:
        """
        处理不回复的情况

        工作流程：
        1. 等待新消息、超时或关闭信号
        2. 根据等待结果更新连续不回复计数
        3. 如果达到阈值，触发回调

        参数:
            reasoning: 不回复的原因
            planner_start_db_time: 规划开始时间
            cycle_timers: 计时器字典

        返回:
            bool: 是否成功处理
        """
        logger.info(f"{self.log_prefix} 决定不回复: {reasoning}")

        observation = self.observations[0] if self.observations else None

        try:
            with Timer("等待新消息", cycle_timers):
                # 等待新消息、超时或关闭信号，并获取结果
                await self._wait_for_new_message(observation, planner_start_db_time, self.log_prefix)
            # 从计时器获取实际等待时间
            current_waiting = cycle_timers.get("等待新消息", 0.0)

            if not self._shutting_down:
                self._lian_xu_bu_hui_fu_ci_shu += 1
                self._lian_xu_deng_dai_shi_jian += current_waiting  # 累加等待时间
                logger.debug(
                    f"{self.log_prefix} 连续不回复计数增加: {self._lian_xu_bu_hui_fu_ci_shu}/{CONSECUTIVE_NO_REPLY_THRESHOLD}, "
                    f"本次等待: {current_waiting:.2f}秒, 累计等待: {self._lian_xu_deng_dai_shi_jian:.2f}秒"
                )

                # 检查是否同时达到次数和时间阈值
                time_threshold = 0.66 * WAITING_TIME_THRESHOLD * CONSECUTIVE_NO_REPLY_THRESHOLD
                if (
                    self._lian_xu_bu_hui_fu_ci_shu >= CONSECUTIVE_NO_REPLY_THRESHOLD
                    and self._lian_xu_deng_dai_shi_jian >= time_threshold
                ):
                    logger.info(
                        f"{self.log_prefix} 连续不回复达到阈值 ({self._lian_xu_bu_hui_fu_ci_shu}次) "
                        f"且累计等待时间达到 {self._lian_xu_deng_dai_shi_jian:.2f}秒 (阈值 {time_threshold}秒)，"
                        f"调用回调请求状态转换"
                    )
                    # 调用回调。注意：这里不重置计数器和时间，依赖回调函数成功改变状态来隐式重置上下文。
                    await self.on_consecutive_no_reply_callback()
                elif self._lian_xu_bu_hui_fu_ci_shu >= CONSECUTIVE_NO_REPLY_THRESHOLD:
                    # 仅次数达到阈值，但时间未达到
                    logger.debug(
                        f"{self.log_prefix} 连续不回复次数达到阈值 ({self._lian_xu_bu_hui_fu_ci_shu}次) "
                        f"但累计等待时间 {self._lian_xu_deng_dai_shi_jian:.2f}秒 未达到时间阈值 ({time_threshold}秒)，暂不调用回调"
                    )
                # else: 次数和时间都未达到阈值，不做处理

            return True

        except asyncio.CancelledError:
            # 如果在等待过程中任务被取消（可能是因为 shutdown）
            logger.info(f"{self.log_prefix} 处理 'no_reply' 时等待被中断 (CancelledError)")
            # 让异常向上传播，由 _hfc_loop 的异常处理逻辑接管
            raise
        except Exception as e:  # 捕获调用管理器或其他地方可能发生的错误
            logger.error(f"{self.log_prefix} 处理 'no_reply' 时发生错误: {e}")
            logger.error(traceback.format_exc())
            # 发生意外错误时，可以选择是否重置计数器，这里选择不重置
            return False  # 表示动作未成功

    async def _wait_for_new_message(self, observation, planner_start_db_time: float, log_prefix: str) -> bool:
        """
        等待新消息 或 检测到关闭信号

        参数:
            observation: 观察实例
            planner_start_db_time: 开始等待的时间
            log_prefix: 日志前缀

        返回:
            bool: 是否检测到新消息 (如果因关闭信号退出则返回 False)
        """
        wait_start_time = time.monotonic()
        while True:
            # --- 在每次循环开始时检查关闭标志 ---
            if self._shutting_down:
                logger.info(f"{log_prefix} 等待新消息时检测到关闭信号，中断等待。")
                return False  # 表示因为关闭而退出
            # -----------------------------------

            # 检查新消息
            if await observation.has_new_messages_since(planner_start_db_time):
                logger.info(f"{log_prefix} 检测到新消息")
                return True

            # 检查超时 (放在检查新消息和关闭之后)
            if time.monotonic() - wait_start_time > WAITING_TIME_THRESHOLD:
                logger.warning(f"{log_prefix} 等待新消息超时({WAITING_TIME_THRESHOLD}秒)")
                return False

            try:
                # 短暂休眠，让其他任务有机会运行，并能更快响应取消或关闭
                await asyncio.sleep(0.5)  # 缩短休眠时间
            except asyncio.CancelledError:
                # 如果在休眠时被取消，再次检查关闭标志
                # 如果是正常关闭，则不需要警告
                if not self._shutting_down:
                    logger.warning(f"{log_prefix} _wait_for_new_message 的休眠被意外取消")
                # 无论如何，重新抛出异常，让上层处理
                raise

    async def _log_cycle_timers(self, cycle_timers: dict, log_prefix: str):
        """记录循环周期的计时器结果"""
        if cycle_timers:
            timer_strings = []
            for name, elapsed in cycle_timers.items():
                formatted_time = f"{elapsed * 1000:.2f}毫秒" if elapsed < 1 else f"{elapsed:.2f}秒"
                timer_strings.append(f"{name}: {formatted_time}")

            if timer_strings:
                # 在记录前检查关闭标志
                if not self._shutting_down:
                    logger.debug(f"{log_prefix} 该次决策耗时: {'; '.join(timer_strings)}")

    async def _get_submind_thinking(self, cycle_timers: dict) -> str:
        """
        获取子思维的思考结果

        返回:
            str: 思考结果，如果思考失败则返回错误信息
        """
        try:
            with Timer("观察", cycle_timers):
                observation = self.observations[0]
                await observation.observe()

            # 获取上一个循环的信息
            # last_cycle = self._cycle_history[-1] if self._cycle_history else None

            with Timer("思考", cycle_timers):
                # 获取上一个循环的动作
                # 传递上一个循环的信息给 do_thinking_before_reply
                current_mind, _past_mind = await self.sub_mind.do_thinking_before_reply(
                    history_cycle=self._cycle_history
                )
                return current_mind
        except Exception as e:
            logger.error(f"{self.log_prefix}[SubMind] 思考失败: {e}")
            logger.error(traceback.format_exc())
            return "[思考时出错]"

    async def _planner(self, current_mind: str, cycle_timers: dict, is_re_planned: bool = False) -> Dict[str, Any]:
        """
        规划器 (Planner): 使用LLM根据上下文决定是否和如何回复。
        重构为：让LLM返回结构化JSON文本，然后在代码中解析。

        参数:
            current_mind: 子思维的当前思考结果
            cycle_timers: 计时器字典
            is_re_planned: 是否为重新规划 (此重构中暂时简化，不处理 is_re_planned 的特殊逻辑)
        """
        logger.info(f"{self.log_prefix}开始想要做什么")

        actions_to_remove_temporarily = []
        # --- 检查历史动作并决定临时移除动作 (逻辑保持不变) ---
        lian_xu_wen_ben_hui_fu = 0
        probability_roll = random.random()
        for cycle in reversed(self._cycle_history):
            if cycle.action_taken:
                if cycle.action_type == "text_reply":
                    lian_xu_wen_ben_hui_fu += 1
                else:
                    break
            if len(self._cycle_history) > 0 and cycle.cycle_id <= self._cycle_history[0].cycle_id + (
                len(self._cycle_history) - 4
            ):
                break
        logger.debug(f"{self.log_prefix}[Planner] 检测到连续文本回复次数: {lian_xu_wen_ben_hui_fu}")

        if lian_xu_wen_ben_hui_fu >= 3:
            logger.info(f"{self.log_prefix}[Planner] 连续回复 >= 3 次，强制移除 text_reply 和 emoji_reply")
            actions_to_remove_temporarily.extend(["text_reply", "emoji_reply"])
        elif lian_xu_wen_ben_hui_fu == 2:
            if probability_roll < 0.8:
                logger.info(f"{self.log_prefix}[Planner] 连续回复 2 次，80% 概率移除 text_reply 和 emoji_reply (触发)")
                actions_to_remove_temporarily.extend(["text_reply", "emoji_reply"])
            else:
                logger.info(
                    f"{self.log_prefix}[Planner] 连续回复 2 次，80% 概率移除 text_reply 和 emoji_reply (未触发)"
                )
        elif lian_xu_wen_ben_hui_fu == 1:
            if probability_roll < 0.4:
                logger.info(f"{self.log_prefix}[Planner] 连续回复 1 次，40% 概率移除 text_reply (触发)")
                actions_to_remove_temporarily.append("text_reply")
            else:
                logger.info(f"{self.log_prefix}[Planner] 连续回复 1 次，40% 概率移除 text_reply (未触发)")
        # --- 结束检查历史动作 ---

        # 获取观察信息
        observation = self.observations[0]
        # if is_re_planned: # 暂时简化，不处理重新规划
        #     await observation.observe()
        observed_messages = observation.talking_message
        observed_messages_str = observation.talking_message_str_truncate

        # --- 使用 LLM 进行决策 (JSON 输出模式) --- #
        action = "no_reply"  # 默认动作
        reasoning = "规划器初始化默认"
        emoji_query = ""
        llm_error = False  # LLM 请求或解析错误标志

        # 获取我们将传递给 prompt 构建器和用于验证的当前可用动作
        current_available_actions = self.action_manager.get_available_actions()

        try:
            # --- 应用临时动作移除 ---
            if actions_to_remove_temporarily:
                self.action_manager.temporarily_remove_actions(actions_to_remove_temporarily)
                # 更新 current_available_actions 以反映移除后的状态
                current_available_actions = self.action_manager.get_available_actions()
                logger.debug(
                    f"{self.log_prefix}[Planner] 临时移除的动作: {actions_to_remove_temporarily}, 当前可用: {list(current_available_actions.keys())}"
                )

            # --- 构建提示词 (调用修改后的 _build_planner_prompt) ---
            # replan_prompt_str = "" # 暂时简化
            # if is_re_planned:
            #     replan_prompt_str = await self._build_replan_prompt(
            #         self._current_cycle.action_type, self._current_cycle.reasoning
            #     )
            prompt = await self._build_planner_prompt(
                observed_messages_str,
                current_mind,
                self.sub_mind.structured_info,
                "",  # replan_prompt_str,
                current_available_actions,  # <--- 传入当前可用动作
            )

            # --- 调用 LLM (普通文本生成) ---
            llm_content = None
            try:
                # 假设 LLMRequest 有 generate_response 方法返回 (content, reasoning, model_name)
                # 我们只需要 content
                # !! 注意：这里假设 self.planner_llm 有 generate_response 方法
                # !! 如果你的 LLMRequest 类使用的是其他方法名，请相应修改
                llm_content, _, _ = await self.planner_llm.generate_response(prompt=prompt)
                logger.debug(f"{self.log_prefix}[Planner] LLM 原始 JSON 响应 (预期): {llm_content}")
            except Exception as req_e:
                logger.error(f"{self.log_prefix}[Planner] LLM 请求执行失败: {req_e}")
                reasoning = f"LLM 请求失败: {req_e}"
                llm_error = True
                # 直接使用默认动作返回错误结果
                action = "no_reply"  # 明确设置为默认值
                emoji_query = ""  # 明确设置为空
                # 不再立即返回，而是继续执行 finally 块以恢复动作
                # return { ... }

            # --- 解析 LLM 返回的 JSON (仅当 LLM 请求未出错时进行) ---
            if not llm_error and llm_content:
                try:
                    # 尝试去除可能的 markdown 代码块标记
                    cleaned_content = (
                        llm_content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                    )
                    if not cleaned_content:
                        raise json.JSONDecodeError("Cleaned content is empty", cleaned_content, 0)
                    parsed_json = json.loads(cleaned_content)

                    # 提取决策，提供默认值
                    extracted_action = parsed_json.get("action", "no_reply")
                    extracted_reasoning = parsed_json.get("reasoning", "LLM未提供理由")
                    extracted_emoji_query = parsed_json.get("emoji_query", "")

                    # 验证动作是否在当前可用列表中
                    # !! 使用调用 prompt 时实际可用的动作列表进行验证
                    if extracted_action not in current_available_actions:
                        logger.warning(
                            f"{self.log_prefix}[Planner] LLM 返回了当前不可用或无效的动作: '{extracted_action}' (可用: {list(current_available_actions.keys())})，将强制使用 'no_reply'"
                        )
                        action = "no_reply"
                        reasoning = f"LLM 返回了当前不可用的动作 '{extracted_action}' (可用: {list(current_available_actions.keys())})。原始理由: {extracted_reasoning}"
                        emoji_query = ""
                        # 检查 no_reply 是否也恰好被移除了 (极端情况)
                        if "no_reply" not in current_available_actions:
                            logger.error(
                                f"{self.log_prefix}[Planner] 严重错误：'no_reply' 动作也不可用！无法执行任何动作。"
                            )
                            action = "error"  # 回退到错误状态
                            reasoning = "无法执行任何有效动作，包括 no_reply"
                            llm_error = True  # 标记为严重错误
                        else:
                            llm_error = False  # 视为逻辑修正而非 LLM 错误
                    else:
                        # 动作有效且可用
                        action = extracted_action
                        reasoning = extracted_reasoning
                        emoji_query = extracted_emoji_query
                        llm_error = False  # 解析成功
                        logger.debug(
                            f"{self.log_prefix}[要做什么]\nPrompt:\n{prompt}\n\n决策结果 (来自JSON): {action}, 理由: {reasoning}, 表情查询: '{emoji_query}'"
                        )

                except json.JSONDecodeError as json_e:
                    logger.warning(
                        f"{self.log_prefix}[Planner] 解析LLM响应JSON失败: {json_e}. LLM原始输出: '{llm_content}'"
                    )
                    reasoning = f"解析LLM响应JSON失败: {json_e}. 将使用默认动作 'no_reply'."
                    action = "no_reply"  # 解析失败则默认不回复
                    emoji_query = ""
                    llm_error = True  # 标记解析错误
                except Exception as parse_e:
                    logger.error(f"{self.log_prefix}[Planner] 处理LLM响应时发生意外错误: {parse_e}")
                    reasoning = f"处理LLM响应时发生意外错误: {parse_e}. 将使用默认动作 'no_reply'."
                    action = "no_reply"
                    emoji_query = ""
                    llm_error = True
            elif not llm_error and not llm_content:
                # LLM 请求成功但返回空内容
                logger.warning(f"{self.log_prefix}[Planner] LLM 返回了空内容。")
                reasoning = "LLM 返回了空内容，使用默认动作 'no_reply'."
                action = "no_reply"
                emoji_query = ""
                llm_error = True  # 标记为空响应错误

            # 如果 llm_error 在此阶段为 True，意味着请求成功但解析失败或返回空
            # 如果 llm_error 在请求阶段就为 True，则跳过了此解析块

        except Exception as outer_e:
            logger.error(f"{self.log_prefix}[Planner] Planner 处理过程中发生意外错误: {outer_e}")
            logger.error(traceback.format_exc())
            action = "error"  # 发生未知错误，标记为 error 动作
            reasoning = f"Planner 内部处理错误: {outer_e}"
            emoji_query = ""
            llm_error = True
        finally:
            # --- 确保动作恢复 ---
            # 检查 self._original_actions_backup 是否有值来判断是否需要恢复
            if self.action_manager._original_actions_backup is not None:
                self.action_manager.restore_actions()
                logger.debug(
                    f"{self.log_prefix}[Planner] 恢复了原始动作集, 当前可用: {list(self.action_manager.get_available_actions().keys())}"
                )
        # --- 结束确保动作恢复 ---

        # --- 概率性忽略文本回复附带的表情 (逻辑保持不变) ---
        if action == "text_reply" and emoji_query:
            logger.debug(f"{self.log_prefix}[Planner] 大模型建议文字回复带表情: '{emoji_query}'")
            if random.random() > EMOJI_SEND_PRO:
                logger.info(
                    f"{self.log_prefix}但是麦麦这次不想加表情 ({1 - EMOJI_SEND_PRO:.0%})，忽略表情 '{emoji_query}'"
                )
                emoji_query = ""  # 清空表情请求
            else:
                logger.info(f"{self.log_prefix}好吧，加上表情 '{emoji_query}'")
        # --- 结束概率性忽略 ---

        # 返回结果字典
        return {
            "action": action,
            "reasoning": reasoning,
            "emoji_query": emoji_query,
            "current_mind": current_mind,
            "observed_messages": observed_messages,
            "llm_error": llm_error,  # 返回错误状态
        }

    async def _get_anchor_message(self) -> Optional[MessageRecv]:
        """
        重构观察到的最后一条消息作为回复的锚点，
        如果重构失败或观察为空，则创建一个占位符。
        """

        try:
            placeholder_id = f"mid_pf_{int(time.time() * 1000)}"
            placeholder_user = UserInfo(
                user_id="system_trigger", user_nickname="System Trigger", platform=self.chat_stream.platform
            )
            placeholder_msg_info = BaseMessageInfo(
                message_id=placeholder_id,
                platform=self.chat_stream.platform,
                group_info=self.chat_stream.group_info,
                user_info=placeholder_user,
                time=time.time(),
            )
            placeholder_msg_dict = {
                "message_info": placeholder_msg_info.to_dict(),
                "processed_plain_text": "[System Trigger Context]",
                "raw_message": "",
                "time": placeholder_msg_info.time,
            }
            anchor_message = MessageRecv(placeholder_msg_dict)
            anchor_message.update_chat_stream(self.chat_stream)
            logger.debug(f"{self.log_prefix} 创建占位符锚点消息: ID={anchor_message.message_info.message_id}")
            return anchor_message

        except Exception as e:
            logger.error(f"{self.log_prefix} Error getting/creating anchor message: {e}")
            logger.error(traceback.format_exc())
            return None

    # --- 发送器 (Sender) --- #
    async def _sender(
        self,
        thinking_id: str,
        anchor_message: MessageRecv,
        response_set: List[str],
        send_emoji: str,  # Emoji query decided by planner or tools
    ):
        """
        发送器 (Sender): 使用 HeartFCSender 实例发送生成的回复。
        处理相关的操作，如发送表情和更新关系。
        """
        logger.info(f"{self.log_prefix}开始发送回复 (使用 HeartFCSender)")

        first_bot_msg: Optional[MessageSending] = None
        try:
            # _send_response_messages 现在将使用 self.sender 内部处理注册和发送
            # 它需要负责创建 MessageThinking 和 MessageSending 对象
            # 并调用 self.sender.register_thinking 和 self.sender.type_and_send_message
            first_bot_msg = await self._send_response_messages(
                anchor_message=anchor_message, response_set=response_set, thinking_id=thinking_id
            )

            if first_bot_msg:
                # --- 处理关联表情(如果指定) --- #
                if send_emoji:
                    logger.info(f"{self.log_prefix}正在发送关联表情: '{send_emoji}'")
                    # 优先使用 first_bot_msg 作为锚点，否则回退到原始锚点
                    emoji_anchor = first_bot_msg
                    await self._handle_emoji(emoji_anchor, response_set, send_emoji)
            else:
                # 如果 _send_response_messages 返回 None，表示在发送前就失败或没有消息可发送
                logger.warning(
                    f"{self.log_prefix}[Sender-{thinking_id}] 未能发送任何回复消息 (_send_response_messages 返回 None)。"
                )
                # 这里可能不需要抛出异常，取决于 _send_response_messages 的具体实现

        except Exception as e:
            # 异常现在由 type_and_send_message 内部处理日志，这里只记录发送流程失败
            logger.error(f"{self.log_prefix}[Sender-{thinking_id}] 发送回复过程中遇到错误: {e}")
            # 思考状态应已在 type_and_send_message 的 finally 块中清理
            # 可以选择重新抛出或根据业务逻辑处理
            # raise RuntimeError(f"发送回复失败: {e}") from e

    async def shutdown(self):
        """优雅关闭HeartFChatting实例，取消活动循环任务"""
        logger.info(f"{self.log_prefix} 正在关闭HeartFChatting...")
        self._shutting_down = True  # <-- 在开始关闭时设置标志位

        # 取消循环任务
        if self._loop_task and not self._loop_task.done():
            logger.info(f"{self.log_prefix} 正在取消HeartFChatting循环任务")
            self._loop_task.cancel()
            try:
                await asyncio.wait_for(self._loop_task, timeout=1.0)
                logger.info(f"{self.log_prefix} HeartFChatting循环任务已取消")
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:
                logger.error(f"{self.log_prefix} 取消循环任务出错: {e}")
        else:
            logger.info(f"{self.log_prefix} 没有活动的HeartFChatting循环任务")

        # 清理状态
        self._loop_active = False
        self._loop_task = None
        if self._processing_lock.locked():
            self._processing_lock.release()
            logger.warning(f"{self.log_prefix} 已释放处理锁")

        logger.info(f"{self.log_prefix} HeartFChatting关闭完成")

    async def _build_replan_prompt(self, action: str, reasoning: str) -> str:
        """构建 Replanner LLM 的提示词"""
        prompt = (await global_prompt_manager.get_prompt_async("replan_prompt")).format(
            action=action,
            reasoning=reasoning,
        )

        # 在记录循环日志前检查关闭标志
        if not self._shutting_down:
            self._current_cycle.complete_cycle()
            self._cycle_history.append(self._current_cycle)

            # 记录循环信息和计时器结果
            timer_strings = []
            for name, elapsed in self._current_cycle.timers.items():
                formatted_time = f"{elapsed * 1000:.2f}毫秒" if elapsed < 1 else f"{elapsed:.2f}秒"
                timer_strings.append(f"{name}: {formatted_time}")

            logger.debug(
                f"{self.log_prefix}  第 #{self._current_cycle.cycle_id}次思考完成,"
                f"耗时: {self._current_cycle.end_time - self._current_cycle.start_time:.2f}秒, "
                f"动作: {self._current_cycle.action_type}"
                + (f"\n计时器详情: {'; '.join(timer_strings)}" if timer_strings else "")
            )

        return prompt

    async def _build_planner_prompt(
        self,
        observed_messages_str: str,
        current_mind: Optional[str],
        structured_info: Dict[str, Any],
        replan_prompt: str,
        current_available_actions: Dict[str, str],
    ) -> str:
        """构建 Planner LLM 的提示词 (获取模板并填充数据)"""
        try:
            # 准备结构化信息块
            structured_info_block = ""
            if structured_info:
                structured_info_block = f"以下是一些额外的信息：\n{structured_info}\n"

            # 准备聊天内容块
            chat_content_block = ""
            if observed_messages_str:
                chat_content_block = "观察到的最新聊天内容如下：\n---\n"
                chat_content_block += observed_messages_str
                chat_content_block += "\n---"
            else:
                chat_content_block = "当前没有观察到新的聊天内容。\n"

            # 准备当前思维块 (修改以匹配模板)
            current_mind_block = ""
            if current_mind:
                # 模板中占位符是 {current_mind_block}，它期望包含"你的内心想法："的前缀
                current_mind_block = f"你的内心想法：\n{current_mind}"
            else:
                current_mind_block = "你的内心想法：\n[没有特别的想法]"

            # 准备循环信息块 (分析最近的活动循环)
            recent_active_cycles = []
            for cycle in reversed(self._cycle_history):
                # 只关心实际执行了动作的循环
                if cycle.action_taken:
                    recent_active_cycles.append(cycle)
                    # 最多找最近的3个活动循环
                    if len(recent_active_cycles) == 3:
                        break

            cycle_info_block = ""
            consecutive_text_replies = 0
            responses_for_prompt = []

            # 检查这最近的活动循环中有多少是连续的文本回复 (从最近的开始看)
            for cycle in recent_active_cycles:
                if cycle.action_type == "text_reply":
                    consecutive_text_replies += 1
                    # 获取回复内容，如果不存在则返回'[空回复]'
                    response_text = cycle.response_info.get("response_text", [])
                    # 使用简单的 join 来格式化回复内容列表
                    formatted_response = "[空回复]" if not response_text else " ".join(response_text)
                    responses_for_prompt.append(formatted_response)
                else:
                    # 一旦遇到非文本回复，连续性中断
                    break

            # 根据连续文本回复的数量构建提示信息
            # 注意: responses_for_prompt 列表是从最近到最远排序的
            if consecutive_text_replies >= 3:  # 如果最近的三个活动都是文本回复
                cycle_info_block = f'你已经连续回复了三条消息（最近: "{responses_for_prompt[0]}"，第二近: "{responses_for_prompt[1]}"，第三近: "{responses_for_prompt[2]}"）。你回复的有点多了，请注意'
            elif consecutive_text_replies == 2:  # 如果最近的两个活动是文本回复
                cycle_info_block = f'你已经连续回复了两条消息（最近: "{responses_for_prompt[0]}"，第二近: "{responses_for_prompt[1]}"），请注意'
            elif consecutive_text_replies == 1:  # 如果最近的一个活动是文本回复
                cycle_info_block = f'你刚刚已经回复一条消息（内容: "{responses_for_prompt[0]}"）'

            # 包装提示块，增加可读性，即使没有连续回复也给个标记
            if cycle_info_block:
                # 模板中占位符是 {cycle_info_block}，它期望包含"【近期回复历史】"的前缀
                cycle_info_block = f"\n【近期回复历史】\n{cycle_info_block}\n"
            else:
                # 如果最近的活动循环不是文本回复，或者没有活动循环
                cycle_info_block = "\n【近期回复历史】\n(最近没有连续文本回复)\n"

            individuality = Individuality.get_instance()
            # 模板中占位符是 {prompt_personality}
            prompt_personality = individuality.get_prompt(x_person=2, level=2)

            # --- 构建可用动作描述 (用于填充模板中的 {action_options_text}) ---
            action_options_text = "当前你可以选择的行动有：\n"
            action_keys = list(current_available_actions.keys())
            for name in action_keys:
                desc = current_available_actions[name]
                action_options_text += f"- '{name}': {desc}\n"

            # --- 选择一个示例动作键 (用于填充模板中的 {example_action}) ---
            example_action_key = action_keys[0] if action_keys else "no_reply"

            # --- 获取提示词模板 ---
            planner_prompt_template = await global_prompt_manager.get_prompt_async("planner_prompt")

            # --- 填充模板 ---
            prompt = planner_prompt_template.format(
                bot_name=global_config.BOT_NICKNAME,
                prompt_personality=prompt_personality,
                structured_info_block=structured_info_block,
                chat_content_block=chat_content_block,
                current_mind_block=current_mind_block,
                replan="",  # 暂时留空 replan 信息
                cycle_info_block=cycle_info_block,
                action_options_text=action_options_text,  # 传入可用动作描述
                example_action=example_action_key,  # 传入示例动作键
            )

            return prompt

        except Exception as e:
            logger.error(f"{self.log_prefix}[Planner] 构建提示词时出错: {e}")
            logger.error(traceback.format_exc())
            return "[构建 Planner Prompt 时出错]"  # 返回错误提示，避免空字符串

    # --- 回复器 (Replier) 的定义 --- #
    async def _replier_work(
        self,
        reason: str,
        anchor_message: MessageRecv,
        thinking_id: str,
    ) -> Optional[List[str]]:
        """
        回复器 (Replier): 核心逻辑，负责生成回复文本。
        (已整合原 HeartFCGenerator 的功能)
        """
        try:
            # 1. 获取情绪影响因子并调整模型温度
            arousal_multiplier = MoodManager.get_instance().get_arousal_multiplier()
            current_temp = global_config.llm_normal["temp"] * arousal_multiplier
            self.model_normal.temperature = current_temp  # 动态调整温度

            # 2. 获取信息捕捉器
            info_catcher = info_catcher_manager.get_info_catcher(thinking_id)

            # 3. 构建 Prompt
            with Timer("构建Prompt", {}):  # 内部计时器，可选保留
                prompt = await prompt_builder.build_prompt(
                    build_mode="focus",
                    reason=reason,
                    current_mind_info=self.sub_mind.current_mind,
                    structured_info=self.sub_mind.structured_info,
                    message_txt="",  # 似乎是固定的空字符串
                    sender_name="",  # 似乎是固定的空字符串
                    chat_stream=anchor_message.chat_stream,
                )

            # 4. 调用 LLM 生成回复
            content = None
            reasoning_content = None
            model_name = "unknown_model"
            try:
                with Timer("LLM生成", {}):  # 内部计时器，可选保留
                    content, reasoning_content, model_name = await self.model_normal.generate_response(prompt)
                # logger.info(f"{self.log_prefix}[Replier-{thinking_id}]\\nPrompt:\\n{prompt}\\n生成回复: {content}\\n")
                # 捕捉 LLM 输出信息
                info_catcher.catch_after_llm_generated(
                    prompt=prompt, response=content, reasoning_content=reasoning_content, model_name=model_name
                )

            except Exception as llm_e:
                # 精简报错信息
                logger.error(f"{self.log_prefix}[Replier-{thinking_id}] LLM 生成失败: {llm_e}")
                return None  # LLM 调用失败则无法生成回复

            # 5. 处理 LLM 响应
            if not content:
                logger.warning(f"{self.log_prefix}[Replier-{thinking_id}] LLM 生成了空内容。")
                return None

            with Timer("处理响应", {}):  # 内部计时器，可选保留
                processed_response = process_llm_response(content)

            if not processed_response:
                logger.warning(f"{self.log_prefix}[Replier-{thinking_id}] 处理后的回复为空。")
                return None

            return processed_response

        except Exception as e:
            # 更通用的错误处理，精简信息
            logger.error(f"{self.log_prefix}[Replier-{thinking_id}] 回复生成意外失败: {e}")
            # logger.error(traceback.format_exc()) # 可以取消注释这行以在调试时查看完整堆栈
            return None

    # --- Methods moved from HeartFCController start ---
    async def _create_thinking_message(self, anchor_message: Optional[MessageRecv]) -> Optional[str]:
        """创建思考消息 (尝试锚定到 anchor_message)"""
        if not anchor_message or not anchor_message.chat_stream:
            logger.error(f"{self.log_prefix} 无法创建思考消息，缺少有效的锚点消息或聊天流。")
            return None

        chat = anchor_message.chat_stream
        messageinfo = anchor_message.message_info
        bot_user_info = UserInfo(
            user_id=global_config.BOT_QQ,
            user_nickname=global_config.BOT_NICKNAME,
            platform=messageinfo.platform,
        )

        thinking_time_point = round(time.time(), 2)
        thinking_id = "mt" + str(thinking_time_point)
        thinking_message = MessageThinking(
            message_id=thinking_id,
            chat_stream=chat,
            bot_user_info=bot_user_info,
            reply=anchor_message,  # 回复的是锚点消息
            thinking_start_time=thinking_time_point,
        )
        # Access MessageManager directly
        await self.heart_fc_sender.register_thinking(thinking_message)
        return thinking_id

    async def _send_response_messages(
        self, anchor_message: Optional[MessageRecv], response_set: List[str], thinking_id: str
    ) -> Optional[MessageSending]:
        """发送回复消息 (尝试锚定到 anchor_message)，使用 HeartFCSender"""
        if not anchor_message or not anchor_message.chat_stream:
            logger.error(f"{self.log_prefix} 无法发送回复，缺少有效的锚点消息或聊天流。")
            return None

        chat = anchor_message.chat_stream
        chat_id = chat.stream_id
        stream_name = chat_manager.get_stream_name(chat_id) or chat_id  # 获取流名称用于日志

        # 检查思考过程是否仍在进行，并获取开始时间
        thinking_start_time = await self.heart_fc_sender.get_thinking_start_time(chat_id, thinking_id)

        if thinking_start_time is None:
            logger.warning(f"[{stream_name}] {thinking_id} 思考过程未找到或已结束，无法发送回复。")
            return None

        # 记录锚点消息ID和回复文本（在发送前记录）
        self._current_cycle.set_response_info(
            response_text=response_set, anchor_message_id=anchor_message.message_info.message_id
        )

        mark_head = False
        first_bot_msg: Optional[MessageSending] = None
        reply_message_ids = []  # 记录实际发送的消息ID
        bot_user_info = UserInfo(
            user_id=global_config.BOT_QQ,
            user_nickname=global_config.BOT_NICKNAME,
            platform=anchor_message.message_info.platform,
        )

        for i, msg_text in enumerate(response_set):
            # 为每个消息片段生成唯一ID
            part_message_id = f"{thinking_id}_{i}"
            message_segment = Seg(type="text", data=msg_text)
            bot_message = MessageSending(
                message_id=part_message_id,  # 使用片段的唯一ID
                chat_stream=chat,
                bot_user_info=bot_user_info,
                sender_info=anchor_message.message_info.user_info,
                message_segment=message_segment,
                reply=anchor_message,  # 回复原始锚点
                is_head=not mark_head,
                is_emoji=False,
                thinking_start_time=thinking_start_time,  # 传递原始思考开始时间
            )
            try:
                if not mark_head:
                    mark_head = True
                    first_bot_msg = bot_message  # 保存第一个成功发送的消息对象
                    await self.heart_fc_sender.type_and_send_message(bot_message, typing=False)
                else:
                    await self.heart_fc_sender.type_and_send_message(bot_message, typing=True)

                reply_message_ids.append(part_message_id)  # 记录我们生成的ID

            except Exception as e:
                logger.error(
                    f"{self.log_prefix}[Sender-{thinking_id}] 发送回复片段 {i} ({part_message_id}) 时失败: {e}"
                )
                # 这里可以选择是继续发送下一个片段还是中止

        # 在尝试发送完所有片段后，完成原始的 thinking_id 状态
        try:
            await self.heart_fc_sender.complete_thinking(chat_id, thinking_id)
        except Exception as e:
            logger.error(f"{self.log_prefix}[Sender-{thinking_id}] 完成思考状态 {thinking_id} 时出错: {e}")

        self._current_cycle.set_response_info(
            response_text=response_set,  # 保留原始文本
            anchor_message_id=anchor_message.message_info.message_id,  # 保留锚点ID
            reply_message_ids=reply_message_ids,  # 添加实际发送的ID列表
        )

        return first_bot_msg  # 返回第一个成功发送的消息对象

    async def _handle_emoji(self, anchor_message: Optional[MessageRecv], response_set: List[str], send_emoji: str = ""):
        """处理表情包 (尝试锚定到 anchor_message)，使用 HeartFCSender"""
        if not anchor_message or not anchor_message.chat_stream:
            logger.error(f"{self.log_prefix} 无法处理表情包，缺少有效的锚点消息或聊天流。")
            return

        chat = anchor_message.chat_stream

        emoji_raw = await emoji_manager.get_emoji_for_text(send_emoji)

        if emoji_raw:
            emoji_path, description = emoji_raw

            emoji_cq = image_path_to_base64(emoji_path)
            thinking_time_point = round(time.time(), 2)  # 用于唯一ID
            message_segment = Seg(type="emoji", data=emoji_cq)
            bot_user_info = UserInfo(
                user_id=global_config.BOT_QQ,
                user_nickname=global_config.BOT_NICKNAME,
                platform=anchor_message.message_info.platform,
            )
            bot_message = MessageSending(
                message_id="me" + str(thinking_time_point),  # 表情消息的唯一ID
                chat_stream=chat,
                bot_user_info=bot_user_info,
                sender_info=anchor_message.message_info.user_info,
                message_segment=message_segment,
                reply=anchor_message,  # 回复原始锚点
                is_head=False,  # 表情通常不是头部消息
                is_emoji=True,
                # 不需要 thinking_start_time
            )

            try:
                await self.heart_fc_sender.send_and_store(bot_message)
            except Exception as e:
                logger.error(f"{self.log_prefix} 发送表情包 {bot_message.message_info.message_id} 时失败: {e}")

    def get_cycle_history(self, last_n: Optional[int] = None) -> List[Dict[str, Any]]:
        """获取循环历史记录

        参数:
            last_n: 获取最近n个循环的信息，如果为None则获取所有历史记录

        返回:
            List[Dict[str, Any]]: 循环历史记录列表
        """
        history = list(self._cycle_history)
        if last_n is not None:
            history = history[-last_n:]
        return [cycle.to_dict() for cycle in history]

    def get_last_cycle_info(self) -> Optional[Dict[str, Any]]:
        """获取最近一个循环的信息"""
        if self._cycle_history:
            return self._cycle_history[-1].to_dict()
        return None
