import asyncio
import contextlib
import json  # <--- 确保导入 json
import random  # <--- 添加导入
import time
import traceback
from collections import deque
from typing import List, Optional, Dict, Any, Deque, Callable, Coroutine
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.message_receive.chat_stream import chat_manager
from rich.traceback import install
from src.common.logger_manager import get_logger
from src.chat.models.utils_model import LLMRequest
from src.config.config import global_config
from src.chat.utils.timer_calculator import Timer
from src.chat.heart_flow.observation.observation import Observation
from src.chat.focus_chat.heartflow_prompt_builder import prompt_builder
from src.chat.focus_chat.heartFC_Cycleinfo import CycleDetail
from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.heart_flow.utils_chat import get_chat_type_and_target_info
from src.chat.focus_chat.info.info_base import InfoBase
from src.chat.focus_chat.info.obs_info import ObsInfo
from src.chat.focus_chat.info.cycle_info import CycleInfo
from src.chat.focus_chat.info.mind_info import MindInfo
from src.chat.focus_chat.info.structured_info import StructuredInfo
from src.chat.focus_chat.info_processors.chattinginfo_processor import ChattingInfoProcessor
from src.chat.focus_chat.info_processors.mind_processor import MindProcessor
from src.chat.heart_flow.observation.memory_observation import MemoryObservation
from src.chat.heart_flow.observation.hfcloop_observation import HFCloopObservation
from src.chat.heart_flow.observation.working_observation import WorkingObservation
from src.chat.focus_chat.info_processors.tool_processor import ToolProcessor
from src.chat.focus_chat.expressors.default_expressor import DefaultExpressor
from src.chat.focus_chat.hfc_utils import create_empty_anchor_message, parse_thinking_id_to_timestamp
from src.chat.focus_chat.memory_activator import MemoryActivator
from src.chat.utils.chat_message_builder import get_raw_msg_before_timestamp_with_chat
from src.plugins.group_nickname.nickname_manager import nickname_manager

install(extra_lines=3)


WAITING_TIME_THRESHOLD = 300  # 等待新消息时间阈值，单位秒

EMOJI_SEND_PRO = 0.3  # 设置一个概率，比如 30% 才真的发

CONSECUTIVE_NO_REPLY_THRESHOLD = 3  # 连续不回复的阈值

logger = get_logger("hfc")  # Logger Name Changed


# 默认动作定义
DEFAULT_ACTIONS = {"no_reply": "不操作，继续浏览", "reply": "表达想法，可以只包含文本、表情或两者都有"}


class ActionManager:
    """动作管理器：控制每次决策可以使用的动作"""

    def __init__(self):
        # 初始化为新的默认动作集
        self._available_actions: Dict[str, str] = DEFAULT_ACTIONS.copy()
        self._original_actions_backup: Optional[Dict[str, str]] = None

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
        observations: list[Observation],
        on_consecutive_no_reply_callback: Callable[[], Coroutine[None, None, None]],
    ):
        """
        HeartFChatting 初始化函数

        参数:
            chat_id: 聊天流唯一标识符(如stream_id)
            observations: 关联的观察列表
            on_consecutive_no_reply_callback: 连续不回复达到阈值时调用的异步回调函数
        """
        # 基础属性
        self.stream_id: str = chat_id  # 聊天流ID
        self.chat_stream: Optional[ChatStream] = None  # 关联的聊天流
        self.observations: List[Observation] = observations  # 关联的观察列表，用于监控聊天流状态
        self.on_consecutive_no_reply_callback = on_consecutive_no_reply_callback

        self.chatting_info_processor = ChattingInfoProcessor()
        self.mind_processor = MindProcessor(subheartflow_id=self.stream_id)

        self.memory_observation = MemoryObservation(observe_id=self.stream_id)
        self.hfcloop_observation = HFCloopObservation(observe_id=self.stream_id)
        self.tool_processor = ToolProcessor(subheartflow_id=self.stream_id)
        self.working_observation = WorkingObservation(observe_id=self.stream_id)
        self.memory_activator = MemoryActivator()

        # 日志前缀
        self.log_prefix: str = str(chat_id)  # Initial default, will be updated

        # --- Initialize attributes (defaults) ---
        self.is_group_chat: bool = False
        self.chat_target_info: Optional[dict] = None
        # --- End Initialization ---
        self.expressor = DefaultExpressor(chat_id=self.stream_id)

        # 动作管理器
        self.action_manager = ActionManager()

        # 初始化状态控制
        self._initialized = False
        self._processing_lock = asyncio.Lock()

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
        self._cycle_history: Deque[CycleDetail] = deque(maxlen=10)  # 保留最近10个循环的信息
        self._current_cycle: Optional[CycleDetail] = None
        self.total_no_reply_count: int = 0  # <--- 新增：连续不回复计数器
        self._shutting_down: bool = False  # <--- 新增：关闭标志位
        self.total_waiting_time: float = 0.0  # <--- 新增：累计等待时间

    async def _initialize(self) -> bool:
        """
        执行懒初始化操作

        功能:
            1. 获取聊天类型(群聊/私聊)和目标信息
            2. 获取聊天流对象
            3. 设置日志前缀

        返回:
            bool: 初始化是否成功

        注意:
            - 如果已经初始化过会直接返回True
            - 需要获取chat_stream对象才能继续后续操作
        """
        # 如果已经初始化过，直接返回成功
        if self._initialized:
            return True

        try:
            self.is_group_chat, self.chat_target_info = await get_chat_type_and_target_info(self.stream_id)
            await self.expressor.initialize()
            self.chat_stream = await asyncio.to_thread(chat_manager.get_stream, self.stream_id)
            self.expressor.chat_stream = self.chat_stream
            self.log_prefix = f"[{chat_manager.get_stream_name(self.stream_id) or self.stream_id}]"
        except Exception as e:
            logger.error(f"[HFC:{self.stream_id}] 初始化HFC时发生错误: {e}")
            return False

        # 标记初始化完成
        self._initialized = True
        logger.debug(f"{self.log_prefix} 初始化完成，准备开始处理消息")
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
                self._current_cycle = CycleDetail(self._cycle_counter)

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

                    # thinking_id 是思考过程的ID，用于标记每一轮思考
                    thinking_id = "tid" + str(round(time.time(), 2))

                    # 主循环：思考->决策->执行

                    action_taken = await self._think_plan_execute_loop(cycle_timers, thinking_id)

                    # 更新循环信息
                    self._current_cycle.set_thinking_id(thinking_id)
                    self._current_cycle.timers = cycle_timers

                    # 防止循环过快消耗资源
                    await _handle_cycle_delay(action_taken, loop_cycle_start_time, self.log_prefix)

                # 完成当前循环并保存历史
                self._current_cycle.complete_cycle()
                self._cycle_history.append(self._current_cycle)

                # 保存CycleInfo到文件
                try:
                    filepath = CycleDetail.save_to_file(self._current_cycle, self.stream_id)
                    logger.info(f"{self.log_prefix} 已保存循环信息到文件: {filepath}")
                except Exception as e:
                    logger.error(f"{self.log_prefix} 保存循环信息到文件时出错: {e}")

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

    async def _think_plan_execute_loop(self, cycle_timers: dict, thinking_id: str) -> tuple[bool, str]:
        try:
            with Timer("观察", cycle_timers):
                await self.observations[0].observe()
                await self.memory_observation.observe()
                await self.working_observation.observe()
                await self.hfcloop_observation.observe()
            observations: List[Observation] = []
            observations.append(self.observations[0])
            observations.append(self.memory_observation)
            observations.append(self.working_observation)
            observations.append(self.hfcloop_observation)

            for observation in observations:
                logger.debug(f"{self.log_prefix} 观察信息: {observation}")

            with Timer("回忆", cycle_timers):
                running_memorys = await self.memory_activator.activate_memory(observations)

            # 记录并行任务开始时间
            parallel_start_time = time.time()
            logger.debug(f"{self.log_prefix} 开始信息处理器并行任务")

            # 并行执行两个任务：思考和工具执行
            with Timer("执行 信息处理器", cycle_timers):
                # 1. 子思维思考 - 不执行工具调用
                think_task = asyncio.create_task(
                    self.mind_processor.process_info(observations=observations, running_memorys=running_memorys)
                )
                logger.debug(f"{self.log_prefix} 启动子思维思考任务")

                # 2. 工具执行器 - 专门处理工具调用
                tool_task = asyncio.create_task(
                    self.tool_processor.process_info(observations=observations, running_memorys=running_memorys)
                )
                logger.debug(f"{self.log_prefix} 启动工具执行任务")

                # 3. 聊天信息处理器
                chatting_info_task = asyncio.create_task(
                    self.chatting_info_processor.process_info(
                        observations=observations, running_memorys=running_memorys
                    )
                )
                logger.debug(f"{self.log_prefix} 启动聊天信息处理器任务")

                # 创建任务完成状态追踪
                tasks = {"思考任务": think_task, "工具任务": tool_task, "聊天信息处理任务": chatting_info_task}
                pending = set(tasks.values())

                # 等待所有任务完成，同时追踪每个任务的完成情况
                results: dict[str, list[InfoBase]] = {}
                while pending:
                    # 等待任务完成
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=1.0)

                    # 记录完成的任务
                    for task in done:
                        for name, t in tasks.items():
                            if task == t:
                                task_end_time = time.time()
                                task_duration = task_end_time - parallel_start_time
                                logger.info(f"{self.log_prefix} {name}已完成，耗时: {task_duration:.2f}秒")
                                results[name] = task.result()
                                break

                    # 如果仍有未完成任务，记录进行中状态
                    if pending:
                        current_time = time.time()
                        elapsed = current_time - parallel_start_time
                        pending_names = [name for name, t in tasks.items() if t in pending]
                        logger.info(
                            f"{self.log_prefix} 并行处理已进行{elapsed:.2f}秒，待完成任务: {', '.join(pending_names)}"
                        )

                # 所有任务完成，从结果中提取数据
                mind_processed_infos = results.get("思考任务", [])
                tool_processed_infos = results.get("工具任务", [])
                chatting_info_processed_infos = results.get("聊天信息处理任务", [])

                # 记录总耗时
                parallel_end_time = time.time()
                total_duration = parallel_end_time - parallel_start_time
                logger.info(f"{self.log_prefix} 思考和工具并行任务全部完成，总耗时: {total_duration:.2f}秒")

                all_plan_info = mind_processed_infos + tool_processed_infos + chatting_info_processed_infos

                logger.debug(f"{self.log_prefix} 所有信息处理器处理后的信息: {all_plan_info}")
            # 串行执行规划器 - 使用刚获取的思考结果
            logger.debug(f"{self.log_prefix} 开始 规划器")
            with Timer("规划器", cycle_timers):
                planner_result = await self._planner(all_plan_info, cycle_timers)

            action = planner_result.get("action", "error")
            action_data = planner_result.get("action_data", {})  # 新增获取动作数据
            reasoning = planner_result.get("reasoning", "未提供理由")

            logger.debug(f"{self.log_prefix} 动作和动作信息: {action}, {action_data}, {reasoning}")

            # 更新循环信息
            self._current_cycle.set_action_info(
                action_type=action,
                action_data=action_data,
                reasoning=reasoning,
                action_taken=True,
            )

            # 处理LLM错误
            if planner_result.get("llm_error"):
                logger.error(f"{self.log_prefix} LLM失败: {reasoning}")
                return False, ""

            # 在此处添加日志记录
            if action == "reply":
                action_str = "回复"
            elif action == "no_reply":
                action_str = "不回复"
            else:
                action_str = "位置动作"

            logger.info(f"{self.log_prefix} 麦麦决定'{action_str}', 原因'{reasoning}'")

            self.hfcloop_observation.add_loop_info(self._current_cycle)

            return await self._handle_action(action, reasoning, action_data, cycle_timers, thinking_id)

        except Exception as e:
            logger.error(f"{self.log_prefix} 并行+串行处理失败: {e}")
            logger.error(traceback.format_exc())
            return False, ""

    async def _handle_action(
        self,
        action: str,
        reasoning: str,
        action_data: dict,
        cycle_timers: dict,
        thinking_id: str,
    ) -> tuple[bool, str]:
        """
        处理规划动作

        参数:
            action: 动作类型
            reasoning: 决策理由
            action_data: 动作数据，包含不同动作需要的参数
            cycle_timers: 计时器字典
            planner_start_db_time: 规划开始时间

        返回:
            tuple[bool, str]: (是否执行了动作, 思考消息ID)
        """
        action_handlers = {
            "reply": self._handle_reply,
            "no_reply": self._handle_no_reply,
        }

        handler = action_handlers.get(action)
        if not handler:
            logger.warning(f"{self.log_prefix} 未知动作: {action}, 原因: {reasoning}")
            return False, ""

        try:
            if action == "reply":
                return await handler(reasoning, action_data, cycle_timers, thinking_id)
            else:  # no_reply
                return await handler(reasoning, cycle_timers, thinking_id)
        except Exception as e:
            logger.error(f"{self.log_prefix} 处理{action}时出错: {e}")
            traceback.print_exc()
            return False, ""

    async def _handle_no_reply(self, reasoning: str, cycle_timers: dict, thinking_id: str) -> bool:
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
                await self._wait_for_new_message(observation, thinking_id, self.log_prefix)
            # 从计时器获取实际等待时间
            current_waiting = cycle_timers.get("等待新消息", 0.0)

            if not self._shutting_down:
                self.total_no_reply_count += 1
                self.total_waiting_time += current_waiting  # 累加等待时间
                logger.debug(
                    f"{self.log_prefix} 连续不回复计数增加: {self.total_no_reply_count}/{CONSECUTIVE_NO_REPLY_THRESHOLD}, "
                    f"本次等待: {current_waiting:.2f}秒, 累计等待: {self.total_waiting_time:.2f}秒"
                )

                # 检查是否同时达到次数和时间阈值
                time_threshold = 0.66 * WAITING_TIME_THRESHOLD * CONSECUTIVE_NO_REPLY_THRESHOLD
                if (
                    self.total_no_reply_count >= CONSECUTIVE_NO_REPLY_THRESHOLD
                    and self.total_waiting_time >= time_threshold
                ):
                    logger.info(
                        f"{self.log_prefix} 连续不回复达到阈值 ({self.total_no_reply_count}次) "
                        f"且累计等待时间达到 {self.total_waiting_time:.2f}秒 (阈值 {time_threshold}秒)，"
                        f"调用回调请求状态转换"
                    )
                    # 调用回调。注意：这里不重置计数器和时间，依赖回调函数成功改变状态来隐式重置上下文。
                    await self.on_consecutive_no_reply_callback()
                elif self.total_no_reply_count >= CONSECUTIVE_NO_REPLY_THRESHOLD:
                    # 仅次数达到阈值，但时间未达到
                    logger.debug(
                        f"{self.log_prefix} 连续不回复次数达到阈值 ({self.total_no_reply_count}次) "
                        f"但累计等待时间 {self.total_waiting_time:.2f}秒 未达到时间阈值 ({time_threshold}秒)，暂不调用回调"
                    )
                # else: 次数和时间都未达到阈值，不做处理

            return True, thinking_id

        except asyncio.CancelledError:
            logger.info(f"{self.log_prefix} 处理 'no_reply' 时等待被中断 (CancelledError)")
            raise
        except Exception as e:  # 捕获调用管理器或其他地方可能发生的错误
            logger.error(f"{self.log_prefix} 处理 'no_reply' 时发生错误: {e}")
            logger.error(traceback.format_exc())
            return False, thinking_id

    async def _wait_for_new_message(self, observation: ChattingObservation, thinking_id: str, log_prefix: str) -> bool:
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

            thinking_id_timestamp = parse_thinking_id_to_timestamp(thinking_id)

            # 检查新消息
            if await observation.has_new_messages_since(thinking_id_timestamp):
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

    async def _planner(self, all_plan_info: List[InfoBase], cycle_timers: dict) -> Dict[str, Any]:
        """
        规划器 (Planner): 使用LLM根据上下文决定是否和如何回复。
        重构为：让LLM返回结构化JSON文本，然后在代码中解析。

        参数:
            current_mind: 子思维的当前思考结果
            cycle_timers: 计时器字典
            is_re_planned: 是否为重新规划 (此重构中暂时简化，不处理 is_re_planned 的特殊逻辑)
        """
        logger.info(f"{self.log_prefix}开始 规划")

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
        for info in all_plan_info:
            if isinstance(info, ObsInfo):
                logger.debug(f"{self.log_prefix} 观察信息: {info}")
                observed_messages = info.get_talking_message()
                observed_messages_str = info.get_talking_message_str_truncate()
                chat_type = info.get_chat_type()
                if chat_type == "group":
                    is_group_chat = True
                else:
                    is_group_chat = False
            elif isinstance(info, MindInfo):
                logger.debug(f"{self.log_prefix} 思维信息: {info}")
                current_mind = info.get_current_mind()
            elif isinstance(info, CycleInfo):
                logger.debug(f"{self.log_prefix} 循环信息: {info}")
                cycle_info = info.get_observe_info()
            elif isinstance(info, StructuredInfo):
                logger.debug(f"{self.log_prefix} 结构化信息: {info}")
                structured_info = info.get_data()

        # --- 使用 LLM 进行决策 (JSON 输出模式) --- #
        action = "no_reply"  # 默认动作
        reasoning = "规划器初始化默认"
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
            # 需要获取用于上下文的历史消息
            message_list_before_now = get_raw_msg_before_timestamp_with_chat(
                chat_id=self.stream_id,
                timestamp=time.time(),  # 使用当前时间作为参考点
                limit=global_config.observation_context_size,  # 使用与 prompt 构建一致的 limit
            )
            # 调用工具函数获取格式化后的绰号字符串
            nickname_injection_str = await nickname_manager.get_nickname_prompt_injection(
                self.chat_stream, message_list_before_now
            )
            # --- 构建提示词 (调用修改后的 PromptBuilder 方法) ---
            prompt = await prompt_builder.build_planner_prompt(
                is_group_chat=is_group_chat,  # <-- Pass HFC state
                chat_target_info=None,
                observed_messages_str=observed_messages_str,  # <-- Pass local variable
                current_mind=current_mind,  # <-- Pass argument
                structured_info=structured_info,  # <-- Pass SubMind info
                current_available_actions=current_available_actions,  # <-- Pass determined actions
                cycle_info=cycle_info,  # <-- Pass cycle info
                nickname_info=nickname_injection_str,  # <-- Pass nickname injection
            )

            # --- 调用 LLM (普通文本生成) ---
            llm_content = None
            try:
                llm_content, _, _ = await self.planner_llm.generate_response(prompt=prompt)
                logger.debug(f"{self.log_prefix}[Planner] LLM 原始 JSON 响应 (预期): {llm_content}")
            except Exception as req_e:
                logger.error(f"{self.log_prefix}[Planner] LLM 请求执行失败: {req_e}")
                reasoning = f"LLM 请求失败: {req_e}"
                llm_error = True
                # 直接使用默认动作返回错误结果
                action = "no_reply"  # 明确设置为默认值

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
                    # extracted_emoji_query = parsed_json.get("emoji_query", "")

                    # 新的reply格式
                    if extracted_action == "reply":
                        action_data = {
                            "text": parsed_json.get("text", []),
                            "emojis": parsed_json.get("emojis", []),
                            "target": parsed_json.get("target", ""),
                        }
                    else:
                        action_data = {}  # 其他动作可能不需要额外数据

                    # 验证动作是否在当前可用列表中
                    # !! 使用调用 prompt 时实际可用的动作列表进行验证
                    if extracted_action not in current_available_actions:
                        logger.warning(
                            f"{self.log_prefix}[Planner] LLM 返回了当前不可用或无效的动作: '{extracted_action}' (可用: {list(current_available_actions.keys())})，将强制使用 'no_reply'"
                        )
                        action = "no_reply"
                        reasoning = f"LLM 返回了当前不可用的动作 '{extracted_action}' (可用: {list(current_available_actions.keys())})。原始理由: {extracted_reasoning}"
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
                        llm_error = False  # 解析成功
                        logger.debug(
                            f"{self.log_prefix}[要做什么]\nPrompt:\n{prompt}\n\n决策结果 (来自JSON): {action}, 理由: {reasoning}"
                        )
                        logger.debug(f"{self.log_prefix}动作信息: '{action_data}'")

                except Exception as json_e:
                    logger.warning(
                        f"{self.log_prefix}[Planner] 解析LLM响应JSON失败: {json_e}. LLM原始输出: '{llm_content}'"
                    )
                    reasoning = f"解析LLM响应JSON失败: {json_e}. 将使用默认动作 'no_reply'."
                    action = "no_reply"  # 解析失败则默认不回复
                    llm_error = True  # 标记解析错误
            elif not llm_error and not llm_content:
                # LLM 请求成功但返回空内容
                logger.warning(f"{self.log_prefix}[Planner] LLM 返回了空内容。")
                reasoning = "LLM 返回了空内容，使用默认动作 'no_reply'."
                action = "no_reply"
                llm_error = True  # 标记为空响应错误

        except Exception as outer_e:
            logger.error(f"{self.log_prefix}[Planner] Planner 处理过程中发生意外错误: {outer_e}")
            traceback.print_exc()
            action = "error"  # 发生未知错误，标记为 error 动作
            reasoning = f"Planner 内部处理错误: {outer_e}"
            llm_error = True
        finally:
            # --- 确保动作恢复 ---
            if self.action_manager._original_actions_backup is not None:
                self.action_manager.restore_actions()
                logger.debug(
                    f"{self.log_prefix}[Planner] 恢复了原始动作集, 当前可用: {list(self.action_manager.get_available_actions().keys())}"
                )

        # --- 概率性忽略文本回复附带的表情 (逻辑保持不变) ---
        try:
            emoji = action_data.get("emojis")
            if action == "reply" and emoji:
                logger.debug(f"{self.log_prefix}[Planner] 大模型建议文字回复带表情: '{emoji}'")
                if random.random() > EMOJI_SEND_PRO:
                    logger.info(f"{self.log_prefix}但是麦麦这次不想加表情 ({1 - EMOJI_SEND_PRO:.0%})，忽略表情 '{emoji}'")
                    action_data["emojis"] = ""  # 清空表情请求
                else:
                    logger.info(f"{self.log_prefix}好吧，加上表情 '{emoji}'")
        except Exception as e:
            logger.error(f"{self.log_prefix}[Planner] 概率性忽略表情时发生错误: {e}")
            traceback.print_exc()
            # --- 结束概率性忽略 ---

        # 返回结果字典
        return {
            "action": action,
            "action_data": action_data,
            "reasoning": reasoning,
            "current_mind": current_mind,
            "observed_messages": observed_messages,
            "llm_error": llm_error,  # 返回错误状态
        }

    async def _handle_reply(
        self, reasoning: str, reply_data: dict, cycle_timers: dict, thinking_id: str
    ) -> tuple[bool, str]:
        """
        处理统一的回复动作 - 可包含文本和表情，顺序任意

        reply_data格式:
        {
            "text": "你好啊"  # 文本内容列表（可选）
            "target": "锚定消息",  # 锚定消息的文本内容
            "emojis": "微笑"  # 表情关键词列表（可选）
        }
        """
        # 重置连续不回复计数器
        self.total_no_reply_count = 0
        self.total_waiting_time = 0.0

        # 从聊天观察获取锚定消息
        observations: ChattingObservation = self.observations[0]
        anchor_message = observations.serch_message_by_text(reply_data["target"])

        # 如果没有找到锚点消息，创建一个占位符
        if not anchor_message:
            logger.info(f"{self.log_prefix} 未找到锚点消息，创建占位符")
            anchor_message = await create_empty_anchor_message(
                self.chat_stream.platform, self.chat_stream.group_info, self.chat_stream
            )
        else:
            anchor_message.update_chat_stream(self.chat_stream)

        success, reply_set = await self.expressor.deal_reply(
            cycle_timers=cycle_timers,
            action_data=reply_data,
            anchor_message=anchor_message,
            reasoning=reasoning,
            thinking_id=thinking_id,
        )

        reply_text = ""
        for reply in reply_set:
            type = reply[0]
            data = reply[1]
            if type == "text":
                reply_text += data
            elif type == "emoji":
                reply_text += data

        self._current_cycle.set_response_info(
            response_text=reply_text,
        )

        return success, reply_text
