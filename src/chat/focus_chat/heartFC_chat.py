import asyncio
import contextlib
import time
import traceback
from collections import deque
from typing import List, Optional, Dict, Any, Deque, Callable, Coroutine
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.message_receive.chat_stream import chat_manager
from rich.traceback import install
from src.common.logger_manager import get_logger
from src.chat.utils.timer_calculator import Timer
from src.chat.heart_flow.observation.observation import Observation
from src.chat.focus_chat.heartFC_Cycleinfo import CycleDetail
from src.chat.focus_chat.info.info_base import InfoBase
from src.chat.focus_chat.info_processors.chattinginfo_processor import ChattingInfoProcessor
from src.chat.focus_chat.info_processors.mind_processor import MindProcessor
from src.chat.heart_flow.observation.memory_observation import MemoryObservation
from src.chat.heart_flow.observation.hfcloop_observation import HFCloopObservation
from src.chat.heart_flow.observation.working_observation import WorkingObservation
from src.chat.focus_chat.info_processors.tool_processor import ToolProcessor
from src.chat.focus_chat.expressors.default_expressor import DefaultExpressor
from src.chat.focus_chat.memory_activator import MemoryActivator
from src.chat.focus_chat.info_processors.base_processor import BaseProcessor
from src.chat.focus_chat.planners.planner import ActionPlanner
from src.chat.focus_chat.planners.action_factory import ActionManager

install(extra_lines=3)


WAITING_TIME_THRESHOLD = 300  # 等待新消息时间阈值，单位秒

EMOJI_SEND_PRO = 0.3  # 设置一个概率，比如 30% 才真的发

CONSECUTIVE_NO_REPLY_THRESHOLD = 3  # 连续不回复的阈值

logger = get_logger("hfc")  # Logger Name Changed


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
        self.log_prefix: str = str(chat_id)  # Initial default, will be updated

        self.memory_observation = MemoryObservation(observe_id=self.stream_id)
        self.hfcloop_observation = HFCloopObservation(observe_id=self.stream_id)
        self.working_observation = WorkingObservation(observe_id=self.stream_id)
        self.memory_activator = MemoryActivator()
        self.expressor = DefaultExpressor(chat_id=self.stream_id)
        self.action_manager = ActionManager()
        self.action_planner = ActionPlanner(log_prefix=self.log_prefix, action_manager=self.action_manager, stream_id=self.stream_id, chat_stream=self.chat_stream)


        # --- 处理器列表 ---
        self.processors: List[BaseProcessor] = []
        self._register_default_processors()

        # 初始化状态控制
        self._initialized = False
        self._processing_lock = asyncio.Lock()

        # 循环控制内部状态
        self._loop_active: bool = False  # 循环是否正在运行
        self._loop_task: Optional[asyncio.Task] = None  # 主循环任务

        # 添加循环信息管理相关的属性
        self._cycle_counter = 0
        self._cycle_history: Deque[CycleDetail] = deque(maxlen=10)  # 保留最近10个循环的信息
        self._current_cycle: Optional[CycleDetail] = None
        self.total_no_reply_count: int = 0  # 连续不回复计数器
        self._shutting_down: bool = False  # 关闭标志位
        self.total_waiting_time: float = 0.0  # 累计等待时间

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

    def _register_default_processors(self):
        """注册默认的信息处理器"""
        self.processors.append(ChattingInfoProcessor())
        self.processors.append(MindProcessor(subheartflow_id=self.stream_id))
        self.processors.append(ToolProcessor(subheartflow_id=self.stream_id))
        logger.info(f"{self.log_prefix} 已注册默认处理器: {[p.__class__.__name__ for p in self.processors]}")

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

        self._loop_task = asyncio.create_task(self._run_focus_chat())
        self._loop_task.add_done_callback(self._handle_loop_completion)

    def _handle_loop_completion(self, task: asyncio.Task):
        """当 _hfc_loop 任务完成时执行的回调。"""
        try:
            exception = task.exception()
            if exception:
                logger.error(f"{self.log_prefix} HeartFChatting: 麦麦脱离了聊天(异常): {exception}")
                logger.error(traceback.format_exc())  # Log full traceback for exceptions
            else:
                logger.info(f"{self.log_prefix} HeartFChatting: 麦麦脱离了聊天 (外部停止)")
        except asyncio.CancelledError:
            logger.info(f"{self.log_prefix} HeartFChatting: 麦麦脱离了聊天(任务取消)")
        finally:
            self._loop_active = False
            self._loop_task = None
            if self._processing_lock.locked():
                logger.warning(f"{self.log_prefix} HeartFChatting: 处理锁在循环结束时仍被锁定，强制释放。")
                self._processing_lock.release()

    async def _run_focus_chat(self):
        """主循环，持续进行计划并可能回复消息，直到被外部取消。"""
        try:
            while True:  # 主循环
                logger.debug(f"{self.log_prefix} 开始第{self._cycle_counter}次循环")
                if self._shutting_down:
                    logger.info(f"{self.log_prefix} 检测到关闭标志，退出 Focus Chat 循环。")
                    break

                # 创建新的循环信息
                self._cycle_counter += 1
                self._current_cycle = CycleDetail(self._cycle_counter)

                # 初始化周期状态
                cycle_timers = {}
                loop_cycle_start_time = time.monotonic()

                # 执行规划和处理阶段
                async with self._get_cycle_context():
                    thinking_id = "tid" + str(round(time.time(), 2))
                    self._current_cycle.set_thinking_id(thinking_id)
                    # 主循环：思考->决策->执行

                    loop_info = await self._observe_process_plan_action_loop(cycle_timers, thinking_id)

                    self._current_cycle.set_loop_info(loop_info)

                    self.hfcloop_observation.add_loop_info(self._current_cycle)
                    self._current_cycle.timers = cycle_timers

                    # 防止循环过快消耗资源
                    await _handle_cycle_delay(
                        loop_info["loop_action_info"]["action_taken"], loop_cycle_start_time, self.log_prefix
                    )

                # 完成当前循环并保存历史
                self._current_cycle.complete_cycle()
                self._cycle_history.append(self._current_cycle)

                # 记录循环信息和计时器结果
                timer_strings = []
                for name, elapsed in cycle_timers.items():
                    formatted_time = f"{elapsed * 1000:.2f}毫秒" if elapsed < 1 else f"{elapsed:.2f}秒"
                    timer_strings.append(f"{name}: {formatted_time}")

                logger.info(
                    f"{self.log_prefix} 第{self._current_cycle.cycle_id}次思考,"
                    f"耗时: {self._current_cycle.end_time - self._current_cycle.start_time:.1f}秒, "
                    f"动作: {self._current_cycle.loop_plan_info['action_result']['action_type']}"
                    + (f"\n详情: {'; '.join(timer_strings)}" if timer_strings else "")
                )

        except asyncio.CancelledError:
            # 设置了关闭标志位后被取消是正常流程
            if not self._shutting_down:
                logger.warning(f"{self.log_prefix} 麦麦Focus聊天模式意外被取消")
            else:
                logger.info(f"{self.log_prefix} 麦麦已离开Focus聊天模式")
        except Exception as e:
            logger.error(f"{self.log_prefix} 麦麦Focus聊天模式意外错误: {e}")
            print(traceback.format_exc())

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

    async def _process_processors(
        self, observations: List[Observation], running_memorys: List[Dict[str, Any]], cycle_timers: dict
    ) -> List[InfoBase]:
        # 记录并行任务开始时间
        parallel_start_time = time.time()
        logger.debug(f"{self.log_prefix} 开始信息处理器并行任务")

        processor_tasks = []
        task_to_name_map = {}

        for processor in self.processors:
            processor_name = processor.__class__.log_prefix
            task = asyncio.create_task(
                processor.process_info(observations=observations, running_memorys=running_memorys)
            )
            processor_tasks.append(task)
            task_to_name_map[task] = processor_name
            logger.debug(f"{self.log_prefix} 启动处理器任务: {processor_name}")

        pending_tasks = set(processor_tasks)
        all_plan_info: List[InfoBase] = []

        while pending_tasks:
            done, pending_tasks = await asyncio.wait(pending_tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                processor_name = task_to_name_map[task]
                task_completed_time = time.time()
                duration_since_parallel_start = task_completed_time - parallel_start_time

                try:
                    # 使用 await task 来获取结果或触发异常
                    result_list = await task
                    logger.info(
                        f"{self.log_prefix} 处理器 {processor_name} 已完成，信息已处理: {duration_since_parallel_start:.2f}秒"
                    )
                    if result_list is not None:
                        all_plan_info.extend(result_list)
                    else:
                        logger.warning(f"{self.log_prefix} 处理器 {processor_name} 返回了 None")
                except Exception as e:
                    logger.error(
                        f"{self.log_prefix} 处理器 {processor_name} 执行失败，耗时 (自并行开始): {duration_since_parallel_start:.2f}秒. 错误: {e}",
                        exc_info=True,
                    )
                    # 即使出错，也认为该任务结束了，已从 pending_tasks 中移除

            if pending_tasks:
                current_progress_time = time.time()
                elapsed_for_log = current_progress_time - parallel_start_time
                pending_names_for_log = [task_to_name_map[t] for t in pending_tasks]
                logger.info(
                    f"{self.log_prefix} 信息处理已进行 {elapsed_for_log:.2f}秒，待完成任务: {', '.join(pending_names_for_log)}"
                )

        # 所有任务完成后的最终日志
        parallel_end_time = time.time()
        total_duration = parallel_end_time - parallel_start_time
        logger.info(f"{self.log_prefix} 所有处理器任务全部完成，总耗时: {total_duration:.2f}秒")
        # logger.debug(f"{self.log_prefix} 所有信息处理器处理后的信息: {all_plan_info}")

        return all_plan_info

    async def _observe_process_plan_action_loop(self, cycle_timers: dict, thinking_id: str) -> tuple[bool, str]:
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

                loop_observation_info = {
                    "observations": observations,
                }

            with Timer("回忆", cycle_timers):
                running_memorys = await self.memory_activator.activate_memory(observations)

            with Timer("执行 信息处理器", cycle_timers):
                all_plan_info = await self._process_processors(observations, running_memorys, cycle_timers)

                loop_processor_info = {
                    "all_plan_info": all_plan_info,
                }

            with Timer("规划器", cycle_timers):
                plan_result = await self.action_planner.plan(all_plan_info, cycle_timers)

                loop_plan_info = {
                    "action_result": plan_result.get("action_result", {}),
                    "current_mind": plan_result.get("current_mind", ""),
                    "observed_messages": plan_result.get("observed_messages", ""),
                }

            with Timer("执行动作", cycle_timers):
                action_type, action_data, reasoning = (
                    plan_result.get("action_result", {}).get("action_type", "error"),
                    plan_result.get("action_result", {}).get("action_data", {}),
                    plan_result.get("action_result", {}).get("reasoning", "未提供理由"),
                )

                # 在此处添加日志记录
                if action_type == "reply":
                    action_str = "回复"
                elif action_type == "no_reply":
                    action_str = "不回复"
                else:
                    action_type = "unknown"
                    action_str = "未知动作"

                logger.info(f"{self.log_prefix} 麦麦决定'{action_str}', 原因'{reasoning}'")

                success, reply_text = await self._handle_action(
                    action_type, reasoning, action_data, cycle_timers, thinking_id
                )

                loop_action_info = {
                    "action_taken": success,
                    "reply_text": reply_text,
                }

            loop_info = {
                "loop_observation_info": loop_observation_info,
                "loop_processor_info": loop_processor_info,
                "loop_plan_info": loop_plan_info,
                "loop_action_info": loop_action_info,
            }

            return loop_info

        except Exception as e:
            logger.error(f"{self.log_prefix} FOCUS聊天处理失败: {e}")
            logger.error(traceback.format_exc())
            return {}

    async def _handle_action(
        self,
        action: str,
        reasoning: str,
        action_data: dict,
        cycle_timers: dict,
        thinking_id: str,
    ) -> tuple[bool, str]:
        """
        处理规划动作，使用动作工厂创建相应的动作处理器

        参数:
            action: 动作类型
            reasoning: 决策理由
            action_data: 动作数据，包含不同动作需要的参数
            cycle_timers: 计时器字典
            thinking_id: 思考ID

        返回:
            tuple[bool, str]: (是否执行了动作, 思考消息ID)
        """
        try:
            # 使用工厂创建动作处理器实例
            action_handler = self.action_manager.create_action(
                action_name=action,
                action_data=action_data,
                reasoning=reasoning,
                cycle_timers=cycle_timers,
                thinking_id=thinking_id,
                observations=self.observations,
                expressor=self.expressor,
                chat_stream=self.chat_stream,
                current_cycle=self._current_cycle,
                log_prefix=self.log_prefix,
                on_consecutive_no_reply_callback=self.on_consecutive_no_reply_callback,
                total_no_reply_count=self.total_no_reply_count,
                total_waiting_time=self.total_waiting_time,
                shutting_down=self._shutting_down,
            )

            if not action_handler:
                logger.warning(f"{self.log_prefix} 未能创建动作处理器: {action}, 原因: {reasoning}")
                return False, ""

            # 处理动作并获取结果
            success, reply_text = await action_handler.handle_action()

            # 更新状态计数器
            if action == "no_reply":
                self.total_no_reply_count = getattr(action_handler, "total_no_reply_count", self.total_no_reply_count)
                self.total_waiting_time = getattr(action_handler, "total_waiting_time", self.total_waiting_time)
            elif action == "reply":
                self.total_no_reply_count = 0
                self.total_waiting_time = 0.0

            return success, reply_text

        except Exception as e:
            logger.error(f"{self.log_prefix} 处理{action}时出错: {e}")
            traceback.print_exc()
            return False, ""

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


