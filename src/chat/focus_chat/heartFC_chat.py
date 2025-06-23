import asyncio
import contextlib
import time
import traceback
from collections import deque
from typing import List, Optional, Dict, Any, Deque, Callable, Awaitable
from src.chat.message_receive.chat_stream import get_chat_manager
from rich.traceback import install
from src.chat.utils.prompt_builder import global_prompt_manager
from src.common.logger import get_logger
from src.chat.utils.timer_calculator import Timer
from src.chat.heart_flow.observation.observation import Observation
from src.chat.focus_chat.heartFC_Cycleinfo import CycleDetail
from src.chat.focus_chat.info.info_base import InfoBase
from src.chat.focus_chat.info_processors.chattinginfo_processor import ChattingInfoProcessor
from src.chat.focus_chat.info_processors.relationship_processor import PersonImpressionpProcessor
from src.chat.focus_chat.info_processors.working_memory_processor import WorkingMemoryProcessor
from src.chat.heart_flow.observation.hfcloop_observation import HFCloopObservation
from src.chat.heart_flow.observation.working_observation import WorkingMemoryObservation
from src.chat.heart_flow.observation.chatting_observation import ChattingObservation
from src.chat.heart_flow.observation.structure_observation import StructureObservation
from src.chat.heart_flow.observation.actions_observation import ActionObservation
from src.chat.focus_chat.info_processors.tool_processor import ToolProcessor
from src.chat.focus_chat.memory_activator import MemoryActivator
from src.chat.focus_chat.info_processors.base_processor import BaseProcessor
from src.chat.focus_chat.info_processors.expression_selector_processor import ExpressionSelectorProcessor
from src.chat.focus_chat.planners.planner_factory import PlannerFactory
from src.chat.focus_chat.planners.modify_actions import ActionModifier
from src.chat.focus_chat.planners.action_manager import ActionManager
from src.config.config import global_config
from src.chat.focus_chat.hfc_performance_logger import HFCPerformanceLogger
from src.chat.focus_chat.hfc_version_manager import get_hfc_version
from src.chat.focus_chat.info.relation_info import RelationInfo
from src.chat.focus_chat.info.expression_selection_info import ExpressionSelectionInfo
from src.chat.focus_chat.info.structured_info import StructuredInfo


install(extra_lines=3)

# 超时常量配置
MEMORY_ACTIVATION_TIMEOUT = 5.0  # 记忆激活任务超时时限（秒）
ACTION_MODIFICATION_TIMEOUT = 15.0  # 动作修改任务超时时限（秒）

# 定义观察器映射：键是观察器名称，值是 (观察器类, 初始化参数)
OBSERVATION_CLASSES = {
    "ChattingObservation": (ChattingObservation, "chat_id"),
    "WorkingMemoryObservation": (WorkingMemoryObservation, "observe_id"),
    "HFCloopObservation": (HFCloopObservation, "observe_id"),
    "StructureObservation": (StructureObservation, "observe_id"),
}

# 定义处理器映射：键是处理器名称，值是 (处理器类, 可选的配置键名)
PROCESSOR_CLASSES = {
    "ChattingInfoProcessor": (ChattingInfoProcessor, None),
    "WorkingMemoryProcessor": (WorkingMemoryProcessor, "working_memory_processor"),
}

# 定义后期处理器映射：在规划后、动作执行前运行的处理器
POST_PLANNING_PROCESSOR_CLASSES = {
    "ToolProcessor": (ToolProcessor, "tool_use_processor"),
    "PersonImpressionpProcessor": (PersonImpressionpProcessor, "person_impression_processor"),
    "ExpressionSelectorProcessor": (ExpressionSelectorProcessor, "expression_selector_processor"),
}

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
    管理一个连续的Focus Chat循环
    用于在特定聊天流中生成回复。
    其生命周期现在由其关联的 SubHeartflow 的 FOCUSED 状态控制。
    """

    def __init__(
        self,
        chat_id: str,
        on_stop_focus_chat: Optional[Callable[[], Awaitable[None]]] = None,
        performance_version: str = None,
    ):
        """
        HeartFChatting 初始化函数

        参数:
            chat_id: 聊天流唯一标识符(如stream_id)
            on_stop_focus_chat: 当收到stop_focus_chat命令时调用的回调函数
            performance_version: 性能记录版本号，用于区分不同启动版本
        """
        # 基础属性
        self.stream_id: str = chat_id  # 聊天流ID
        self.chat_stream = get_chat_manager().get_stream(self.stream_id)
        self.log_prefix = f"[{get_chat_manager().get_stream_name(self.stream_id) or self.stream_id}]"

        self.memory_activator = MemoryActivator()

        # 初始化观察器
        self.observations: List[Observation] = []
        self._register_observations()

        # 根据配置文件和默认规则确定启用的处理器
        config_processor_settings = global_config.focus_chat_processor
        self.enabled_processor_names = []

        for proc_name, (_proc_class, config_key) in PROCESSOR_CLASSES.items():
            # 检查处理器是否应该启用
            if not config_key or getattr(config_processor_settings, config_key, True):
                self.enabled_processor_names.append(proc_name)

        # 初始化后期处理器（规划后执行的处理器）
        self.enabled_post_planning_processor_names = []
        for proc_name, (_proc_class, config_key) in POST_PLANNING_PROCESSOR_CLASSES.items():
            # 对于关系处理器，需要同时检查两个配置项
            if proc_name == "PersonImpressionpProcessor":
                if global_config.relationship.enable_relationship and getattr(
                    config_processor_settings, config_key, True
                ):
                    self.enabled_post_planning_processor_names.append(proc_name)
            else:
                # 其他后期处理器的逻辑
                if not config_key or getattr(config_processor_settings, config_key, True):
                    self.enabled_post_planning_processor_names.append(proc_name)

        # logger.info(f"{self.log_prefix} 将启用的处理器: {self.enabled_processor_names}")
        # logger.info(f"{self.log_prefix} 将启用的后期处理器: {self.enabled_post_planning_processor_names}")

        self.processors: List[BaseProcessor] = []
        self._register_default_processors()

        # 初始化后期处理器
        self.post_planning_processors: List[BaseProcessor] = []
        self._register_post_planning_processors()

        self.action_manager = ActionManager()
        self.action_planner = PlannerFactory.create_planner(
            log_prefix=self.log_prefix, action_manager=self.action_manager
        )
        self.action_modifier = ActionModifier(action_manager=self.action_manager)
        self.action_observation = ActionObservation(observe_id=self.stream_id)
        self.action_observation.set_action_manager(self.action_manager)

        self._processing_lock = asyncio.Lock()

        # 循环控制内部状态
        self._loop_active: bool = False  # 循环是否正在运行
        self._loop_task: Optional[asyncio.Task] = None  # 主循环任务

        # 添加循环信息管理相关的属性
        self._cycle_counter = 0
        self._cycle_history: Deque[CycleDetail] = deque(maxlen=10)  # 保留最近10个循环的信息
        self._current_cycle_detail: Optional[CycleDetail] = None
        self._shutting_down: bool = False  # 关闭标志位

        # 存储回调函数
        self.on_stop_focus_chat = on_stop_focus_chat

        # 初始化性能记录器
        # 如果没有指定版本号，则使用全局版本管理器的版本号
        actual_version = performance_version or get_hfc_version()
        self.performance_logger = HFCPerformanceLogger(chat_id, actual_version)

    def _register_observations(self):
        """注册所有观察器"""
        self.observations = []  # 清空已有的

        for name, (observation_class, param_name) in OBSERVATION_CLASSES.items():
            try:
                # 检查是否需要跳过WorkingMemoryObservation
                if name == "WorkingMemoryObservation":
                    # 如果工作记忆处理器被禁用，则跳过WorkingMemoryObservation
                    if not global_config.focus_chat_processor.working_memory_processor:
                        logger.debug(f"{self.log_prefix} 工作记忆处理器已禁用，跳过注册观察器 {name}")
                        continue

                # 根据参数名使用正确的参数
                kwargs = {param_name: self.stream_id}
                observation = observation_class(**kwargs)
                self.observations.append(observation)
                logger.debug(f"{self.log_prefix} 注册观察器 {name}")
            except Exception as e:
                logger.error(f"{self.log_prefix} 观察器 {name} 构造失败: {e}")

        if self.observations:
            logger.info(f"{self.log_prefix} 已注册观察器: {[o.__class__.__name__ for o in self.observations]}")
        else:
            logger.warning(f"{self.log_prefix} 没有注册任何观察器")

    def _register_default_processors(self):
        """根据 self.enabled_processor_names 注册信息处理器"""
        self.processors = []  # 清空已有的

        for name in self.enabled_processor_names:  # 'name' is "ChattingInfoProcessor", etc.
            processor_info = PROCESSOR_CLASSES.get(name)  # processor_info is (ProcessorClass, config_key)
            if processor_info:
                processor_actual_class = processor_info[0]  # 获取实际的类定义
                # 根据处理器类名判断是否需要 subheartflow_id
                if name in [
                    "WorkingMemoryProcessor",
                ]:
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
            logger.info(f"{self.log_prefix} 已注册处理器: {[p.__class__.__name__ for p in self.processors]}")
        else:
            logger.warning(f"{self.log_prefix} 没有注册任何处理器。这可能是由于配置错误或所有处理器都被禁用了。")

    def _register_post_planning_processors(self):
        """根据 self.enabled_post_planning_processor_names 注册后期处理器"""
        self.post_planning_processors = []  # 清空已有的

        for name in self.enabled_post_planning_processor_names:  # 'name' is "PersonImpressionpProcessor", etc.
            processor_info = POST_PLANNING_PROCESSOR_CLASSES.get(name)  # processor_info is (ProcessorClass, config_key)
            if processor_info:
                processor_actual_class = processor_info[0]  # 获取实际的类定义
                # 根据处理器类名判断是否需要 subheartflow_id
                if name in [
                    "ToolProcessor",
                    "PersonImpressionpProcessor",
                    "ExpressionSelectorProcessor",
                ]:
                    self.post_planning_processors.append(processor_actual_class(subheartflow_id=self.stream_id))
                else:
                    # 对于POST_PLANNING_PROCESSOR_CLASSES中定义但此处未明确处理构造的处理器
                    # (例如, 新增了一个处理器到POST_PLANNING_PROCESSOR_CLASSES, 它不需要id, 也不叫PersonImpressionpProcessor)
                    try:
                        self.post_planning_processors.append(processor_actual_class())  # 尝试无参构造
                        logger.debug(f"{self.log_prefix} 注册后期处理器 {name} (尝试无参构造).")
                    except TypeError:
                        logger.error(
                            f"{self.log_prefix} 后期处理器 {name} 构造失败。它可能需要参数（如 subheartflow_id）但未在注册逻辑中明确处理。"
                        )
            else:
                # 这理论上不应该发生，因为 enabled_post_planning_processor_names 是从 POST_PLANNING_PROCESSOR_CLASSES 的键生成的
                logger.warning(
                    f"{self.log_prefix} 在 POST_PLANNING_PROCESSOR_CLASSES 中未找到名为 '{name}' 的处理器定义，将跳过注册。"
                )

        if self.post_planning_processors:
            logger.info(
                f"{self.log_prefix} 已注册后期处理器: {[p.__class__.__name__ for p in self.post_planning_processors]}"
            )
        else:
            logger.warning(
                f"{self.log_prefix} 没有注册任何后期处理器。这可能是由于配置错误或所有后期处理器都被禁用了。"
            )

    async def start(self):
        """检查是否需要启动主循环，如果未激活则启动。"""
        logger.debug(f"{self.log_prefix} 开始启动 HeartFChatting")

        # 如果循环已经激活，直接返回
        if self._loop_active:
            logger.debug(f"{self.log_prefix} HeartFChatting 已激活，无需重复启动")
            return

        try:
            # 标记为活动状态，防止重复启动
            self._loop_active = True

            # 检查是否已有任务在运行（理论上不应该，因为 _loop_active=False）
            if self._loop_task and not self._loop_task.done():
                logger.warning(f"{self.log_prefix} 发现之前的循环任务仍在运行（不符合预期）。取消旧任务。")
                self._loop_task.cancel()
                try:
                    # 等待旧任务确实被取消
                    await asyncio.wait_for(self._loop_task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass  # 忽略取消或超时错误
                except Exception as e:
                    logger.warning(f"{self.log_prefix} 等待旧任务取消时出错: {e}")
                self._loop_task = None  # 清理旧任务引用

            logger.debug(f"{self.log_prefix} 创建新的 HeartFChatting 主循环任务")
            self._loop_task = asyncio.create_task(self._run_focus_chat())
            self._loop_task.add_done_callback(self._handle_loop_completion)
            logger.debug(f"{self.log_prefix} HeartFChatting 启动完成")

        except Exception as e:
            # 启动失败时重置状态
            self._loop_active = False
            self._loop_task = None
            logger.error(f"{self.log_prefix} HeartFChatting 启动失败: {e}")
            raise

    def _handle_loop_completion(self, task: asyncio.Task):
        """当 _hfc_loop 任务完成时执行的回调。"""
        try:
            exception = task.exception()
            if exception:
                logger.error(f"{self.log_prefix} HeartFChatting: 脱离了聊天(异常): {exception}")
                logger.error(traceback.format_exc())  # Log full traceback for exceptions
            else:
                logger.info(f"{self.log_prefix} HeartFChatting: 脱离了聊天 (外部停止)")
        except asyncio.CancelledError:
            logger.info(f"{self.log_prefix} HeartFChatting: 脱离了聊天(任务取消)")
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

                # 检查关闭标志
                if self._shutting_down:
                    logger.info(f"{self.log_prefix} 检测到关闭标志，退出 Focus Chat 循环。")
                    break

                # 创建新的循环信息
                self._cycle_counter += 1
                self._current_cycle_detail = CycleDetail(self._cycle_counter)
                self._current_cycle_detail.prefix = self.log_prefix

                # 初始化周期状态
                cycle_timers = {}
                loop_cycle_start_time = time.monotonic()

                # 执行规划和处理阶段
                try:
                    async with self._get_cycle_context():
                        thinking_id = "tid" + str(round(time.time(), 2))
                        self._current_cycle_detail.set_thinking_id(thinking_id)

                        # 使用异步上下文管理器处理消息
                        try:
                            async with global_prompt_manager.async_message_scope(
                                self.chat_stream.context.get_template_name()
                            ):
                                # 在上下文内部检查关闭状态
                                if self._shutting_down:
                                    logger.info(f"{self.log_prefix} 在处理上下文中检测到关闭信号，退出")
                                    break

                                logger.debug(f"模板 {self.chat_stream.context.get_template_name()}")
                                loop_info = await self._observe_process_plan_action_loop(cycle_timers, thinking_id)

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

                        except asyncio.CancelledError:
                            logger.info(f"{self.log_prefix} 处理上下文时任务被取消")
                            break
                        except Exception as e:
                            logger.error(f"{self.log_prefix} 处理上下文时出错: {e}")
                            # 为当前循环设置错误状态，防止后续重复报错
                            error_loop_info = {
                                "loop_observation_info": {},
                                "loop_processor_info": {},
                                "loop_plan_info": {
                                    "action_result": {
                                        "action_type": "error",
                                        "action_data": {},
                                        "reasoning": f"上下文处理失败: {e}",
                                    },
                                    "observed_messages": "",
                                },
                                "loop_action_info": {
                                    "action_taken": False,
                                    "reply_text": "",
                                    "command": "",
                                    "taken_time": time.time(),
                                },
                            }
                            self._current_cycle_detail.set_loop_info(error_loop_info)
                            self._current_cycle_detail.complete_cycle()

                            # 上下文处理失败，跳过当前循环
                            await asyncio.sleep(1)
                            continue

                        self._current_cycle_detail.set_loop_info(loop_info)

                        # 从observations列表中获取HFCloopObservation
                        hfcloop_observation = next(
                            (obs for obs in self.observations if isinstance(obs, HFCloopObservation)), None
                        )
                        if hfcloop_observation:
                            hfcloop_observation.add_loop_info(self._current_cycle_detail)
                        else:
                            logger.warning(f"{self.log_prefix} 未找到HFCloopObservation实例")

                        self._current_cycle_detail.timers = cycle_timers

                        # 防止循环过快消耗资源
                        await _handle_cycle_delay(
                            loop_info["loop_action_info"]["action_taken"], loop_cycle_start_time, self.log_prefix
                        )

                    # 完成当前循环并保存历史
                    self._current_cycle_detail.complete_cycle()
                    self._cycle_history.append(self._current_cycle_detail)

                    # 记录循环信息和计时器结果
                    timer_strings = []
                    for name, elapsed in cycle_timers.items():
                        formatted_time = f"{elapsed * 1000:.2f}毫秒" if elapsed < 1 else f"{elapsed:.2f}秒"
                        timer_strings.append(f"{name}: {formatted_time}")

                    # 新增：输出每个处理器的耗时
                    processor_time_costs = self._current_cycle_detail.loop_processor_info.get(
                        "processor_time_costs", {}
                    )
                    processor_time_strings = []
                    for pname, ptime in processor_time_costs.items():
                        formatted_ptime = f"{ptime * 1000:.2f}毫秒" if ptime < 1 else f"{ptime:.2f}秒"
                        processor_time_strings.append(f"{pname}: {formatted_ptime}")
                    processor_time_log = (
                        ("\n前处理器耗时: " + "; ".join(processor_time_strings)) if processor_time_strings else ""
                    )

                    # 新增：输出每个后处理器的耗时
                    post_processor_time_costs = self._current_cycle_detail.loop_post_processor_info.get(
                        "post_processor_time_costs", {}
                    )
                    post_processor_time_strings = []
                    for pname, ptime in post_processor_time_costs.items():
                        formatted_ptime = f"{ptime * 1000:.2f}毫秒" if ptime < 1 else f"{ptime:.2f}秒"
                        post_processor_time_strings.append(f"{pname}: {formatted_ptime}")
                    post_processor_time_log = (
                        ("\n后处理器耗时: " + "; ".join(post_processor_time_strings))
                        if post_processor_time_strings
                        else ""
                    )

                    logger.info(
                        f"{self.log_prefix} 第{self._current_cycle_detail.cycle_id}次思考,"
                        f"耗时: {self._current_cycle_detail.end_time - self._current_cycle_detail.start_time:.1f}秒, "
                        f"动作: {self._current_cycle_detail.loop_plan_info.get('action_result', {}).get('action_type', '未知动作')}"
                        + (f"\n详情: {'; '.join(timer_strings)}" if timer_strings else "")
                        + processor_time_log
                        + post_processor_time_log
                    )

                    # 记录性能数据
                    try:
                        action_result = self._current_cycle_detail.loop_plan_info.get("action_result", {})
                        cycle_performance_data = {
                            "cycle_id": self._current_cycle_detail.cycle_id,
                            "action_type": action_result.get("action_type", "unknown"),
                            "total_time": self._current_cycle_detail.end_time - self._current_cycle_detail.start_time,
                            "step_times": cycle_timers.copy(),
                            "processor_time_costs": processor_time_costs,  # 前处理器时间
                            "post_processor_time_costs": post_processor_time_costs,  # 后处理器时间
                            "reasoning": action_result.get("reasoning", ""),
                            "success": self._current_cycle_detail.loop_action_info.get("action_taken", False),
                        }
                        self.performance_logger.record_cycle(cycle_performance_data)
                    except Exception as perf_e:
                        logger.warning(f"{self.log_prefix} 记录性能数据失败: {perf_e}")

                    await asyncio.sleep(global_config.focus_chat.think_interval)

                except asyncio.CancelledError:
                    logger.info(f"{self.log_prefix} 循环处理时任务被取消")
                    break
                except Exception as e:
                    logger.error(f"{self.log_prefix} 循环处理时出错: {e}")
                    logger.error(traceback.format_exc())

                    # 如果_current_cycle_detail存在但未完成，为其设置错误状态
                    if self._current_cycle_detail and not hasattr(self._current_cycle_detail, "end_time"):
                        error_loop_info = {
                            "loop_observation_info": {},
                            "loop_processor_info": {},
                            "loop_plan_info": {
                                "action_result": {
                                    "action_type": "error",
                                    "action_data": {},
                                    "reasoning": f"循环处理失败: {e}",
                                },
                                "observed_messages": "",
                            },
                            "loop_action_info": {
                                "action_taken": False,
                                "reply_text": "",
                                "command": "",
                                "taken_time": time.time(),
                            },
                        }
                        try:
                            self._current_cycle_detail.set_loop_info(error_loop_info)
                            self._current_cycle_detail.complete_cycle()
                        except Exception as inner_e:
                            logger.error(f"{self.log_prefix} 设置错误状态时出错: {inner_e}")

                    await asyncio.sleep(1)  # 出错后等待一秒再继续

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

    async def _process_processors(self, observations: List[Observation]) -> tuple[List[InfoBase], Dict[str, float]]:
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
                    proc.process_info(observations=observations),
                    timeout=global_config.focus_chat.processor_max_time,
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
                    logger.info(
                        f"{self.log_prefix} 处理器 {processor_name} 超时（>{global_config.focus_chat.processor_max_time}s），已跳过"
                    )
                    processor_time_costs[processor_name] = global_config.focus_chat.processor_max_time
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

    async def _process_post_planning_processors(self, observations: List[Observation], action_data: dict) -> dict:
        """
        处理后期处理器（规划后执行的处理器）
        包括：关系处理器、表达选择器、记忆激活器

        参数:
            observations: 观察器列表
            action_data: 原始动作数据

        返回:
            dict: 更新后的动作数据
        """
        logger.info(f"{self.log_prefix} 开始执行后期处理器")

        # 创建所有后期任务
        task_list = []
        task_to_name_map = {}

        # 添加后期处理器任务
        for processor in self.post_planning_processors:
            processor_name = processor.__class__.__name__

            async def run_processor_with_timeout(proc=processor):
                return await asyncio.wait_for(
                    proc.process_info(observations=observations),
                    timeout=global_config.focus_chat.processor_max_time,
                )

            task = asyncio.create_task(run_processor_with_timeout())
            task_list.append(task)
            task_to_name_map[task] = ("processor", processor_name)
            logger.info(f"{self.log_prefix} 启动后期处理器任务: {processor_name}")

        # 添加记忆激活器任务
        async def run_memory_with_timeout():
            return await asyncio.wait_for(
                self.memory_activator.activate_memory(observations),
                timeout=MEMORY_ACTIVATION_TIMEOUT,
            )

        memory_task = asyncio.create_task(run_memory_with_timeout())
        task_list.append(memory_task)
        task_to_name_map[memory_task] = ("memory", "MemoryActivator")
        logger.info(f"{self.log_prefix} 启动记忆激活器任务")

        # 如果没有任何后期任务，直接返回
        if not task_list:
            logger.info(f"{self.log_prefix} 没有启用的后期处理器或记忆激活器")
            return action_data

        # 等待所有任务完成
        pending_tasks = set(task_list)
        all_post_plan_info = []
        running_memorys = []

        while pending_tasks:
            done, pending_tasks = await asyncio.wait(pending_tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                task_type, task_name = task_to_name_map[task]

                try:
                    result = await task

                    if task_type == "processor":
                        logger.info(f"{self.log_prefix} 后期处理器 {task_name} 已完成!")
                        if result is not None:
                            all_post_plan_info.extend(result)
                        else:
                            logger.warning(f"{self.log_prefix} 后期处理器 {task_name} 返回了 None")
                    elif task_type == "memory":
                        logger.info(f"{self.log_prefix} 记忆激活器已完成!")
                        if result is not None:
                            running_memorys = result
                        else:
                            logger.warning(f"{self.log_prefix} 记忆激活器返回了 None")
                            running_memorys = []

                except asyncio.TimeoutError:
                    if task_type == "processor":
                        logger.warning(
                            f"{self.log_prefix} 后期处理器 {task_name} 超时（>{global_config.focus_chat.processor_max_time}s），已跳过"
                        )
                    elif task_type == "memory":
                        logger.warning(f"{self.log_prefix} 记忆激活器超时（>{MEMORY_ACTIVATION_TIMEOUT}s），已跳过")
                        running_memorys = []
                except Exception as e:
                    if task_type == "processor":
                        logger.error(
                            f"{self.log_prefix} 后期处理器 {task_name} 执行失败. 错误: {e}",
                            exc_info=True,
                        )
                    elif task_type == "memory":
                        logger.error(f"{self.log_prefix} 记忆激活器执行失败. 错误: {e}", exc_info=True)
                        running_memorys = []

        # 将后期处理器的结果整合到 action_data 中
        updated_action_data = action_data.copy()

        relation_info = ""
        selected_expressions = []
        structured_info = ""

        for info in all_post_plan_info:
            if isinstance(info, RelationInfo):
                relation_info = info.get_processed_info()
            elif isinstance(info, ExpressionSelectionInfo):
                selected_expressions = info.get_expressions_for_action_data()
            elif isinstance(info, StructuredInfo):
                structured_info = info.get_processed_info()

        if relation_info:
            updated_action_data["relation_info_block"] = relation_info

        if selected_expressions:
            updated_action_data["selected_expressions"] = selected_expressions

        if structured_info:
            updated_action_data["structured_info"] = structured_info

        # 特殊处理running_memorys
        if running_memorys:
            memory_str = "以下是当前在聊天中，你回忆起的记忆：\n"
            for running_memory in running_memorys:
                memory_str += f"{running_memory['content']}\n"
            updated_action_data["memory_block"] = memory_str
            logger.info(f"{self.log_prefix} 添加了 {len(running_memorys)} 个激活的记忆到action_data")

        if all_post_plan_info or running_memorys:
            logger.info(
                f"{self.log_prefix} 后期处理完成，产生了 {len(all_post_plan_info)} 个信息项和 {len(running_memorys)} 个记忆"
            )

        return updated_action_data

    async def _process_post_planning_processors_with_timing(
        self, observations: List[Observation], action_data: dict
    ) -> tuple[dict, dict]:
        """
        处理后期处理器（规划后执行的处理器）并收集详细时间统计
        包括：关系处理器、表达选择器、记忆激活器

        参数:
            observations: 观察器列表
            action_data: 原始动作数据

        返回:
            tuple[dict, dict]: (更新后的动作数据, 后处理器时间统计)
        """
        logger.info(f"{self.log_prefix} 开始执行后期处理器（带详细统计）")

        # 创建所有后期任务
        task_list = []
        task_to_name_map = {}
        task_start_times = {}
        post_processor_time_costs = {}

        # 添加后期处理器任务
        for processor in self.post_planning_processors:
            processor_name = processor.__class__.__name__

            async def run_processor_with_timeout_and_timing(proc=processor, name=processor_name):
                start_time = time.time()
                try:
                    result = await asyncio.wait_for(
                        proc.process_info(observations=observations),
                        timeout=global_config.focus_chat.processor_max_time,
                    )
                    end_time = time.time()
                    post_processor_time_costs[name] = end_time - start_time
                    logger.debug(f"{self.log_prefix} 后期处理器 {name} 耗时: {end_time - start_time:.3f}秒")
                    return result
                except Exception as e:
                    end_time = time.time()
                    post_processor_time_costs[name] = end_time - start_time
                    logger.warning(f"{self.log_prefix} 后期处理器 {name} 执行异常，耗时: {end_time - start_time:.3f}秒")
                    raise e

            task = asyncio.create_task(run_processor_with_timeout_and_timing())
            task_list.append(task)
            task_to_name_map[task] = ("processor", processor_name)
            task_start_times[task] = time.time()
            logger.info(f"{self.log_prefix} 启动后期处理器任务: {processor_name}")

        # 添加记忆激活器任务
        async def run_memory_with_timeout_and_timing():
            start_time = time.time()
            try:
                result = await asyncio.wait_for(
                    self.memory_activator.activate_memory(observations),
                    timeout=MEMORY_ACTIVATION_TIMEOUT,
                )
                end_time = time.time()
                post_processor_time_costs["MemoryActivator"] = end_time - start_time
                logger.debug(f"{self.log_prefix} 记忆激活器耗时: {end_time - start_time:.3f}秒")
                return result
            except Exception as e:
                end_time = time.time()
                post_processor_time_costs["MemoryActivator"] = end_time - start_time
                logger.warning(f"{self.log_prefix} 记忆激活器执行异常，耗时: {end_time - start_time:.3f}秒")
                raise e

        memory_task = asyncio.create_task(run_memory_with_timeout_and_timing())
        task_list.append(memory_task)
        task_to_name_map[memory_task] = ("memory", "MemoryActivator")
        task_start_times[memory_task] = time.time()
        logger.info(f"{self.log_prefix} 启动记忆激活器任务")

        # 如果没有任何后期任务，直接返回
        if not task_list:
            logger.info(f"{self.log_prefix} 没有启用的后期处理器或记忆激活器")
            return action_data, {}

        # 等待所有任务完成
        pending_tasks = set(task_list)
        all_post_plan_info = []
        running_memorys = []

        while pending_tasks:
            done, pending_tasks = await asyncio.wait(pending_tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                task_type, task_name = task_to_name_map[task]

                try:
                    result = await task

                    if task_type == "processor":
                        logger.info(f"{self.log_prefix} 后期处理器 {task_name} 已完成!")
                        if result is not None:
                            all_post_plan_info.extend(result)
                        else:
                            logger.warning(f"{self.log_prefix} 后期处理器 {task_name} 返回了 None")
                    elif task_type == "memory":
                        logger.info(f"{self.log_prefix} 记忆激活器已完成!")
                        if result is not None:
                            running_memorys = result
                        else:
                            logger.warning(f"{self.log_prefix} 记忆激活器返回了 None")
                            running_memorys = []

                except asyncio.TimeoutError:
                    # 对于超时任务，记录已用时间
                    elapsed_time = time.time() - task_start_times[task]
                    if task_type == "processor":
                        post_processor_time_costs[task_name] = elapsed_time
                        logger.warning(
                            f"{self.log_prefix} 后期处理器 {task_name} 超时（>{global_config.focus_chat.processor_max_time}s），已跳过，耗时: {elapsed_time:.3f}秒"
                        )
                    elif task_type == "memory":
                        post_processor_time_costs["MemoryActivator"] = elapsed_time
                        logger.warning(
                            f"{self.log_prefix} 记忆激活器超时（>{MEMORY_ACTIVATION_TIMEOUT}s），已跳过，耗时: {elapsed_time:.3f}秒"
                        )
                        running_memorys = []
                except Exception as e:
                    # 对于异常任务，记录已用时间
                    elapsed_time = time.time() - task_start_times[task]
                    if task_type == "processor":
                        post_processor_time_costs[task_name] = elapsed_time
                        logger.error(
                            f"{self.log_prefix} 后期处理器 {task_name} 执行失败，耗时: {elapsed_time:.3f}秒. 错误: {e}",
                            exc_info=True,
                        )
                    elif task_type == "memory":
                        post_processor_time_costs["MemoryActivator"] = elapsed_time
                        logger.error(
                            f"{self.log_prefix} 记忆激活器执行失败，耗时: {elapsed_time:.3f}秒. 错误: {e}",
                            exc_info=True,
                        )
                        running_memorys = []

        # 将后期处理器的结果整合到 action_data 中
        updated_action_data = action_data.copy()

        relation_info = ""
        selected_expressions = []
        structured_info = ""

        for info in all_post_plan_info:
            if isinstance(info, RelationInfo):
                relation_info = info.get_processed_info()
            elif isinstance(info, ExpressionSelectionInfo):
                selected_expressions = info.get_expressions_for_action_data()
            elif isinstance(info, StructuredInfo):
                structured_info = info.get_processed_info()

        if relation_info:
            updated_action_data["relation_info_block"] = relation_info

        if selected_expressions:
            updated_action_data["selected_expressions"] = selected_expressions

        if structured_info:
            updated_action_data["structured_info"] = structured_info

        # 特殊处理running_memorys
        if running_memorys:
            memory_str = "以下是当前在聊天中，你回忆起的记忆：\n"
            for running_memory in running_memorys:
                memory_str += f"{running_memory['content']}\n"
            updated_action_data["memory_block"] = memory_str
            logger.info(f"{self.log_prefix} 添加了 {len(running_memorys)} 个激活的记忆到action_data")

        if all_post_plan_info or running_memorys:
            logger.info(
                f"{self.log_prefix} 后期处理完成，产生了 {len(all_post_plan_info)} 个信息项和 {len(running_memorys)} 个记忆"
            )

        # 输出详细统计信息
        if post_processor_time_costs:
            stats_str = ", ".join(
                [f"{name}: {time_cost:.3f}s" for name, time_cost in post_processor_time_costs.items()]
            )
            logger.info(f"{self.log_prefix} 后期处理器详细耗时统计: {stats_str}")

        return updated_action_data, post_processor_time_costs

    async def _observe_process_plan_action_loop(self, cycle_timers: dict, thinking_id: str) -> dict:
        try:
            loop_start_time = time.time()
            with Timer("观察", cycle_timers):
                # 执行所有观察器的观察
                for observation in self.observations:
                    await observation.observe()

                loop_observation_info = {
                    "observations": self.observations,
                }

            # 根据配置决定是否并行执行调整动作、回忆和处理器阶段

            # 并行执行调整动作、回忆和处理器阶段
            with Timer("并行调整动作、处理", cycle_timers):
                # 创建并行任务
                async def modify_actions_task():
                    # 调用完整的动作修改流程
                    await self.action_modifier.modify_actions(
                        observations=self.observations,
                    )

                    await self.action_observation.observe()
                    self.observations.append(self.action_observation)
                    return True

                # 创建两个并行任务，为LLM调用添加超时保护
                action_modify_task = asyncio.create_task(
                    asyncio.wait_for(modify_actions_task(), timeout=ACTION_MODIFICATION_TIMEOUT)
                )
                processor_task = asyncio.create_task(self._process_processors(self.observations))

                # 等待两个任务完成，使用超时保护和详细错误处理
                action_modify_result = None
                all_plan_info = []
                processor_time_costs = {}

                try:
                    action_modify_result, (all_plan_info, processor_time_costs) = await asyncio.gather(
                        action_modify_task, processor_task, return_exceptions=True
                    )

                    # 检查各个任务的结果
                    if isinstance(action_modify_result, Exception):
                        if isinstance(action_modify_result, asyncio.TimeoutError):
                            logger.error(f"{self.log_prefix} 动作修改任务超时")
                        else:
                            logger.error(f"{self.log_prefix} 动作修改任务失败: {action_modify_result}")

                    processor_result = (all_plan_info, processor_time_costs)
                    if isinstance(processor_result, Exception):
                        if isinstance(processor_result, asyncio.TimeoutError):
                            logger.error(f"{self.log_prefix} 处理器任务超时")
                        else:
                            logger.error(f"{self.log_prefix} 处理器任务失败: {processor_result}")
                        all_plan_info = []
                        processor_time_costs = {}
                    else:
                        all_plan_info, processor_time_costs = processor_result

                except Exception as e:
                    logger.error(f"{self.log_prefix} 并行任务gather失败: {e}")
                    # 设置默认值以继续执行
                    all_plan_info = []
                    processor_time_costs = {}

            loop_processor_info = {
                "all_plan_info": all_plan_info,
                "processor_time_costs": processor_time_costs,
            }

            logger.debug(f"{self.log_prefix} 并行阶段完成，准备进入规划器，plan_info数量: {len(all_plan_info)}")

            with Timer("规划器", cycle_timers):
                plan_result = await self.action_planner.plan(all_plan_info, [], loop_start_time)

                loop_plan_info = {
                    "action_result": plan_result.get("action_result", {}),
                    "observed_messages": plan_result.get("observed_messages", ""),
                }

            # 修正：将后期处理器从执行动作Timer中分离出来
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

            logger.debug(f"{self.log_prefix} 麦麦想要：'{action_str}'")

            # 添加：单独计时后期处理器，并收集详细统计
            post_processor_time_costs = {}
            if action_type != "no_reply":
                with Timer("后期处理器", cycle_timers):
                    logger.debug(f"{self.log_prefix} 执行后期处理器（动作类型: {action_type}）")
                    # 记录详细的后处理器时间
                    post_start_time = time.time()
                    action_data, post_processor_time_costs = await self._process_post_planning_processors_with_timing(
                        self.observations, action_data
                    )
                    post_end_time = time.time()
                    logger.info(f"{self.log_prefix} 后期处理器总耗时: {post_end_time - post_start_time:.3f}秒")
            else:
                logger.debug(f"{self.log_prefix} 跳过后期处理器（动作类型: {action_type}）")

            # 修正：纯动作执行计时
            with Timer("动作执行", cycle_timers):
                success, reply_text, command = await self._handle_action(
                    action_type, reasoning, action_data, cycle_timers, thinking_id
                )

                loop_action_info = {
                    "action_taken": success,
                    "reply_text": reply_text,
                    "command": command,
                    "taken_time": time.time(),
                }

            # 添加后处理器统计到loop_info
            loop_post_processor_info = {
                "post_processor_time_costs": post_processor_time_costs,
            }

            loop_info = {
                "loop_observation_info": loop_observation_info,
                "loop_processor_info": loop_processor_info,
                "loop_plan_info": loop_plan_info,
                "loop_action_info": loop_action_info,
                "loop_post_processor_info": loop_post_processor_info,  # 新增
            }

            return loop_info

        except Exception as e:
            logger.error(f"{self.log_prefix} FOCUS聊天处理失败: {e}")
            logger.error(traceback.format_exc())
            return {
                "loop_observation_info": {},
                "loop_processor_info": {},
                "loop_plan_info": {
                    "action_result": {"action_type": "error", "action_data": {}, "reasoning": f"处理失败: {e}"},
                    "observed_messages": "",
                },
                "loop_action_info": {"action_taken": False, "reply_text": "", "command": "", "taken_time": time.time()},
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

            # 检查action_data中是否有系统命令，优先使用系统命令
            if "_system_command" in action_data:
                command = action_data["_system_command"]
                logger.debug(f"{self.log_prefix} 从action_data中获取系统命令: {command}")

            logger.debug(f"{self.log_prefix} 麦麦执行了'{action}', 返回结果'{success}', '{reply_text}', '{command}'")

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

        # 完成性能统计
        try:
            self.performance_logger.finalize_session()
            logger.info(f"{self.log_prefix} 性能统计已完成")
        except Exception as e:
            logger.warning(f"{self.log_prefix} 完成性能统计时出错: {e}")

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
