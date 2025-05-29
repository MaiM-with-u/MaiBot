import asyncio
import contextlib
import time
import traceback
from collections import deque
from typing import List, Optional, Dict, Any, Deque, Callable, Awaitable
from src.chat.message_receive.chat_stream import ChatStream
from src.chat.message_receive.chat_stream import chat_manager
from rich.traceback import install
from src.chat.utils.prompt_builder import global_prompt_manager
from src.common.logger_manager import get_logger
from src.chat.utils.timer_calculator import Timer
from src.chat.heart_flow.observation.observation import Observation
from src.chat.focus_chat.heartFC_Cycleinfo import CycleDetail
from src.chat.focus_chat.info.info_base import InfoBase
from src.chat.focus_chat.info_processors.chattinginfo_processor import ChattingInfoProcessor
from src.chat.focus_chat.info_processors.mind_processor import MindProcessor
from src.chat.focus_chat.info_processors.working_memory_processor import WorkingMemoryProcessor

# from src.chat.focus_chat.info_processors.action_processor import ActionProcessor
from src.chat.heart_flow.observation.hfcloop_observation import HFCloopObservation
from src.chat.heart_flow.observation.working_observation import WorkingMemoryObservation
from src.chat.heart_flow.observation.structure_observation import StructureObservation
from src.chat.heart_flow.observation.actions_observation import ActionObservation
from src.chat.focus_chat.info_processors.tool_processor import ToolProcessor
from src.chat.focus_chat.expressors.default_expressor import DefaultExpressor
from src.chat.focus_chat.memory_activator import MemoryActivator
from src.chat.focus_chat.info_processors.base_processor import BaseProcessor
from src.chat.focus_chat.info_processors.self_processor import SelfProcessor
from src.chat.focus_chat.planners.planner import ActionPlanner
from src.chat.focus_chat.planners.modify_actions import ActionModifier
from src.chat.focus_chat.planners.action_manager import ActionManager
from src.chat.focus_chat.working_memory.working_memory import WorkingMemory
from src.config.config import global_config

install(extra_lines=3)


# 定义处理器映射：键是处理器名称，值是 (处理器类, 可选的配置键名)
# 如果配置键名为 None，则该处理器默认启用且不能通过 focus_chat_processor 配置禁用
PROCESSOR_CLASSES = {
    "ChattingInfoProcessor": (ChattingInfoProcessor, None),
    "MindProcessor": (MindProcessor, None),
    "ToolProcessor": (ToolProcessor, "tool_use_processor"),
    "WorkingMemoryProcessor": (WorkingMemoryProcessor, "working_memory_processor"),
    "SelfProcessor": (SelfProcessor, "self_identify_processor"),
    # "ActionProcessor": (ActionProcessor, "action_processor"),  # 这个处理器不需要配置键名，默认启用
}


WAITING_TIME_THRESHOLD = 300  # 等待新消息时间阈值，单位秒

EMOJI_SEND_PRO = 0.3  # 设置一个概率，比如 30% 才真的发

CONSECUTIVE_NO_REPLY_THRESHOLD = 3  # 连续不回复的阈值

logger = get_logger("hfc")  # Logger Name Changed

# 设定处理器超时时间（秒）
PROCESSOR_TIMEOUT = 30


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
    管理一个连续的Focus Chat循环
    用于在特定聊天流中生成回复。
    其生命周期现在由其关联的 SubHeartflow 的 FOCUSED 状态控制。
    """

    def __init__(
        self,
        chat_id: str,
        observations: list[Observation],
        on_stop_focus_chat: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        """
        HeartFChatting 初始化函数

        参数:
            chat_id: 聊天流唯一标识符(如stream_id)
            observations: 关联的观察列表
            on_stop_focus_chat: 当收到stop_focus_chat命令时调用的回调函数
        """
        # 基础属性
        self.stream_id: str = chat_id  # 聊天流ID
        self.chat_stream: Optional[ChatStream] = None  # 关联的聊天流
        self.log_prefix: str = str(chat_id)  # Initial default, will be updated
        self.hfcloop_observation = HFCloopObservation(observe_id=self.stream_id)
        self.chatting_observation = observations[0]
        self.structure_observation = StructureObservation(observe_id=self.stream_id)

        self.memory_activator = MemoryActivator()
        self.working_memory = WorkingMemory(chat_id=self.stream_id)
        self.working_observation = WorkingMemoryObservation(
            observe_id=self.stream_id, working_memory=self.working_memory
        )

        # 根据配置文件和默认规则确定启用的处理器
        self.enabled_processor_names: List[str] = []
        config_processor_settings = global_config.focus_chat_processor

        for proc_name, (_proc_class, config_key) in PROCESSOR_CLASSES.items():
            if config_key:  # 此处理器可通过配置控制
                if getattr(config_processor_settings, config_key, True):  # 默认启用 (如果配置中未指定该键)
                    self.enabled_processor_names.append(proc_name)
            else:  # 此处理器不在配置映射中 (config_key is None)，默认启用
                self.enabled_processor_names.append(proc_name)

        logger.info(f"{self.log_prefix} 将启用的处理器: {self.enabled_processor_names}")
        self.processors: List[BaseProcessor] = []
        self._register_default_processors()

        self.expressor = DefaultExpressor(chat_id=self.stream_id)
        self.action_manager = ActionManager()
        self.action_planner = ActionPlanner(log_prefix=self.log_prefix, action_manager=self.action_manager)
        self.action_modifier = ActionModifier(action_manager=self.action_manager)
        self.action_observation = ActionObservation(observe_id=self.stream_id)

        self.action_observation.set_action_manager(self.action_manager)

        self.all_observations = observations

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
        self._shutting_down: bool = False  # 关闭标志位

        # 存储回调函数
        self.on_stop_focus_chat = on_stop_focus_chat

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
        """根据 self.enabled_processor_names 注册信息处理器"""
        self.processors = []  # 清空已有的

        for name in self.enabled_processor_names:  # 'name' is "ChattingInfoProcessor", etc.
            processor_info = PROCESSOR_CLASSES.get(name)  # processor_info is (ProcessorClass, config_key)
            if processor_info:
                processor_actual_class = processor_info[0]  # 获取实际的类定义
                # 根据处理器类名判断是否需要 subheartflow_id
                if name in ["MindProcessor", "ToolProcessor", "WorkingMemoryProcessor", "SelfProcessor"]:
                    self.processors.append(processor_actual_class(subheartflow_id=self.stream_id))
                elif name == "ChattingInfoProcessor":
                    self.processors.append(processor_actual_class())
                else:
                    # 对于PROCESSOR_CLASSES中定义但此处未明确处理构造的处理器
                    # (例如, 新增了一个处理器到PROCESSOR_CLASSES, 它不需要id, 也不叫ChattingInfoProcessor)
                    try:
                        self.processors.append(processor_actual_class())  # 尝试无参构造
                        logger.debug(f"{self.log_prefix} 注册处理器 {name} (尝试无参构造).")
                    except TypeError:
                        logger.error(
                            f"{self.log_prefix} 处理器 {name} 构造失败。它可能需要参数（如 subheartflow_id）但未在注册逻辑中明确处理。"
                        )
            else:
                # 这理论上不应该发生，因为 enabled_processor_names 是从 PROCESSOR_CLASSES 的键生成的
                logger.warning(
                    f"{self.log_prefix} 在 PROCESSOR_CLASSES 中未找到名为 '{name}' 的处理器定义，将跳过注册。"
                )

        if self.processors:
            logger.info(
                f"{self.log_prefix} 已根据配置和默认规则注册处理器: {[p.__class__.__name__ for p in self.processors]}"
            )
        else:
            logger.warning(f"{self.log_prefix} 没有注册任何处理器。这可能是由于配置错误或所有处理器都被禁用了。")

    async def start(self):
        """
        启动 HeartFChatting 的主循环。
        注意：调用此方法前必须确保已经成功初始化。
        """
        logger.info(f"{self.log_prefix} 开始认真聊天(HFC)...")
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
                    async with global_prompt_manager.async_message_scope(self.chat_stream.context.get_template_name()):
                        logger.debug(f"模板 {self.chat_stream.context.get_template_name()}")
                        loop_info = await self._observe_process_plan_action_loop(cycle_timers, thinking_id)

                        print(loop_info["loop_action_info"]["command"])
                        if loop_info["loop_action_info"]["command"] == "stop_focus_chat":
                            logger.info(f"{self.log_prefix} 麦麦决定停止专注聊天")
                            # 如果设置了回调函数，则调用它
                            if self.on_stop_focus_chat:
                                try:
                                    await self.on_stop_focus_chat()
                                    logger.info(f"{self.log_prefix} 成功调用回调函数处理停止专注聊天")
                                except Exception as e:
                                    logger.error(f"{self.log_prefix} 调用停止专注聊天回调函数时出错: {e}")
                                    logger.error(traceback.format_exc())
                            break

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

                # 新增：输出每个处理器的耗时
                processor_time_costs = self._current_cycle.loop_processor_info.get("processor_time_costs", {})
                processor_time_strings = []
                for pname, ptime in processor_time_costs.items():
                    formatted_ptime = f"{ptime * 1000:.2f}毫秒" if ptime < 1 else f"{ptime:.2f}秒"
                    processor_time_strings.append(f"{pname}: {formatted_ptime}")
                processor_time_log = (
                    ("\n各处理器耗时: " + "; ".join(processor_time_strings)) if processor_time_strings else ""
                )

                logger.info(
                    f"{self.log_prefix} 第{self._current_cycle.cycle_id}次思考,"
                    f"耗时: {self._current_cycle.end_time - self._current_cycle.start_time:.1f}秒, "
                    f"动作: {self._current_cycle.loop_plan_info['action_result']['action_type']}"
                    + (f"\n详情: {'; '.join(timer_strings)}" if timer_strings else "")
                    + processor_time_log
                )

                await asyncio.sleep(global_config.focus_chat.think_interval)

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
    ) -> tuple[List[InfoBase], Dict[str, float]]:
        # 记录并行任务开始时间
        parallel_start_time = time.time()
        logger.debug(f"{self.log_prefix} 开始信息处理器并行任务")

        processor_tasks = []
        task_to_name_map = {}
        processor_time_costs = {}  # 新增: 记录每个处理器耗时

        for processor in self.processors:
            processor_name = processor.__class__.log_prefix

            async def run_with_timeout(proc=processor):
                return await asyncio.wait_for(
                    proc.process_info(observations=observations, running_memorys=running_memorys),
                    timeout=PROCESSOR_TIMEOUT,
                )

            task = asyncio.create_task(run_with_timeout())
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
                    result_list = await task
                    logger.info(f"{self.log_prefix} 处理器 {processor_name} 已完成!")
                    if result_list is not None:
                        all_plan_info.extend(result_list)
                    else:
                        logger.warning(f"{self.log_prefix} 处理器 {processor_name} 返回了 None")
                    # 记录耗时
                    processor_time_costs[processor_name] = duration_since_parallel_start
                except asyncio.TimeoutError:
                    logger.info(f"{self.log_prefix} 处理器 {processor_name} 超时（>{PROCESSOR_TIMEOUT}s），已跳过")
                    processor_time_costs[processor_name] = PROCESSOR_TIMEOUT
                except Exception as e:
                    logger.error(
                        f"{self.log_prefix} 处理器 {processor_name} 执行失败，耗时 (自并行开始): {duration_since_parallel_start:.2f}秒. 错误: {e}",
                        exc_info=True,
                    )
                    traceback.print_exc()
                    processor_time_costs[processor_name] = duration_since_parallel_start

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

        return all_plan_info, processor_time_costs

    async def _observe_process_plan_action_loop(self, cycle_timers: dict, thinking_id: str) -> dict:
        try:
            with Timer("观察", cycle_timers):
                await self.chatting_observation.observe()
                await self.working_observation.observe()
                await self.hfcloop_observation.observe()
                await self.structure_observation.observe()
                observations: List[Observation] = []
                observations.append(self.chatting_observation)
                observations.append(self.working_observation)
                observations.append(self.hfcloop_observation)
                observations.append(self.structure_observation)

                loop_observation_info = {
                    "observations": observations,
                }

                self.all_observations = observations

            with Timer("回忆", cycle_timers):
                running_memorys = await self.memory_activator.activate_memory(observations)

            with Timer("调整动作", cycle_timers):
                # 处理特殊的观察
                await self.action_modifier.modify_actions(observations=observations, running_memorys=running_memorys)
                await self.action_observation.observe()
                observations.append(self.action_observation)

            with Timer("执行 信息处理器", cycle_timers):
                all_plan_info, processor_time_costs = await self._process_processors(
                    observations, running_memorys, cycle_timers
                )

                loop_processor_info = {
                    "all_plan_info": all_plan_info,
                    "processor_time_costs": processor_time_costs,
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

                if action_type == "reply":
                    action_str = "回复"
                elif action_type == "no_reply":
                    action_str = "不回复"
                else:
                    action_str = action_type

                logger.debug(f"{self.log_prefix} 麦麦想要：'{action_str}', 原因'{reasoning}'")

                success, reply_text, command = await self._handle_action(
                    action_type, reasoning, action_data, cycle_timers, thinking_id
                )

                loop_action_info = {
                    "action_taken": success,
                    "reply_text": reply_text,
                    "command": command,
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
            return {
                "loop_observation_info": {},
                "loop_processor_info": {},
                "loop_plan_info": {},
                "loop_action_info": {"action_taken": False, "reply_text": "", "command": ""},
            }

    async def _handle_action(
        self,
        action: str,
        reasoning: str,
        action_data: dict,
        cycle_timers: dict,
        thinking_id: str,
    ) -> tuple[bool, str, str]:
        """
        处理规划动作，使用动作工厂创建相应的动作处理器

        参数:
            action: 动作类型
            reasoning: 决策理由
            action_data: 动作数据，包含不同动作需要的参数
            cycle_timers: 计时器字典
            thinking_id: 思考ID

        返回:
            tuple[bool, str, str]: (是否执行了动作, 思考消息ID, 命令)
        """
        try:
            # 使用工厂创建动作处理器实例
            try:
                action_handler = self.action_manager.create_action(
                    action_name=action,
                    action_data=action_data,
                    reasoning=reasoning,
                    cycle_timers=cycle_timers,
                    thinking_id=thinking_id,
                    observations=self.all_observations,
                    expressor=self.expressor,
                    chat_stream=self.chat_stream,
                    log_prefix=self.log_prefix,
                    shutting_down=self._shutting_down,
                )
            except Exception as e:
                logger.error(f"{self.log_prefix} 创建动作处理器时出错: {e}")
                traceback.print_exc()
                return False, "", ""

            if not action_handler:
                logger.warning(f"{self.log_prefix} 未能创建动作处理器: {action}, 原因: {reasoning}")
                return False, "", ""

            # 处理动作并获取结果
            result = await action_handler.handle_action()
            if len(result) == 3:
                success, reply_text, command = result
            else:
                success, reply_text = result
                command = ""
            logger.debug(
                f"{self.log_prefix} 麦麦执行了'{action}', 原因'{reasoning}'，返回结果'{success}', '{reply_text}', '{command}'"
            )
            return success, reply_text, command

        except Exception as e:
            logger.error(f"{self.log_prefix} 处理{action}时出错: {e}")
            traceback.print_exc()
            return False, "", ""

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
