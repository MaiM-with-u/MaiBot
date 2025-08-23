import asyncio
import time
import traceback
import math
import random
from typing import List, Optional, Dict, Any, Tuple, TYPE_CHECKING
from rich.traceback import install
from collections import deque

from src.config.config import global_config
from src.common.logger import get_logger
from src.common.data_models.info_data_model import ActionPlannerInfo
from src.chat.message_receive.chat_stream import ChatStream, get_chat_manager
from src.chat.utils.prompt_builder import global_prompt_manager
from src.chat.utils.timer_calculator import Timer
from src.chat.planner_actions.planner import ActionPlanner
from src.chat.planner_actions.action_modifier import ActionModifier
from src.chat.planner_actions.action_manager import ActionManager
from src.chat.heart_flow.hfc_utils import CycleDetail
from src.chat.heart_flow.hfc_utils import send_typing, stop_typing
from src.chat.memory_system.Hippocampus import hippocampus_manager
from src.chat.frequency_control.talk_frequency_control import talk_frequency_control
from src.chat.frequency_control.focus_value_control import focus_value_control
from src.chat.express.expression_learner import expression_learner_manager
from src.person_info.relationship_builder_manager import relationship_builder_manager
from src.person_info.person_info import Person
from src.plugin_system.base.component_types import ChatMode, ActionInfo
from src.plugin_system.core.event_manager import event_manager
from src.plugin_system.apis import generator_api, send_api, message_api, database_api
from src.mais4u.mai_think import mai_thinking_manager
from src.mais4u.s4u_config import s4u_config
from src.chat.utils.chat_message_builder import build_readable_messages_with_id, build_readable_actions, get_actions_by_timestamp_with_chat, get_raw_msg_before_timestamp_with_chat

if TYPE_CHECKING:
    from src.common.data_models.database_data_model import DatabaseMessages


ERROR_LOOP_INFO = {
    "loop_plan_info": {
        "action_result": {
            "action_type": "error",
            "action_data": {},
            "reasoning": "循环处理失败",
        },
    },
    "loop_action_info": {
        "action_taken": False,
        "reply_text": "",
        "command": "",
        "taken_time": time.time(),
    },
}


install(extra_lines=3)

# 注释：原来的动作修改超时常量已移除，因为改为顺序执行

logger = get_logger("hfc")  # Logger Name Changed


class HeartFChatting:
    """
    管理一个连续的Focus Chat循环
    用于在特定聊天流中生成回复。
    其生命周期现在由其关联的 SubHeartflow 的 FOCUSED 状态控制。
    """

    def __init__(self, chat_id: str):
        """
        HeartFChatting 初始化函数

        参数:
            chat_id: 聊天流唯一标识符(如stream_id)
            on_stop_focus_chat: 当收到stop_focus_chat命令时调用的回调函数
            performance_version: 性能记录版本号，用于区分不同启动版本
        """
        # 基础属性
        self.stream_id: str = chat_id  # 聊天流ID
        self.chat_stream: ChatStream = get_chat_manager().get_stream(self.stream_id)  # type: ignore
        if not self.chat_stream:
            raise ValueError(f"无法找到聊天流: {self.stream_id}")
        self.log_prefix = f"[{get_chat_manager().get_stream_name(self.stream_id) or self.stream_id}]"

        self.relationship_builder = relationship_builder_manager.get_or_create_builder(self.stream_id)
        self.expression_learner = expression_learner_manager.get_expression_learner(self.stream_id)

        self.talk_frequency_control = talk_frequency_control.get_talk_frequency_control(self.stream_id)
        self.focus_value_control = focus_value_control.get_focus_value_control(self.stream_id)

        self.action_manager = ActionManager()
        self.action_planner = ActionPlanner(chat_id=self.stream_id, action_manager=self.action_manager)
        self.action_modifier = ActionModifier(action_manager=self.action_manager, chat_id=self.stream_id)

        # 循环控制内部状态
        self.running: bool = False
        self._loop_task: Optional[asyncio.Task] = None  # 主循环任务

        # 添加循环信息管理相关的属性
        self.history_loop: List[CycleDetail] = []
        self._cycle_counter = 0
        self._current_cycle_detail: CycleDetail = None  # type: ignore

        self.reply_timeout_count = 0
        self.plan_timeout_count = 0

        self.last_read_time = time.time() - 10

        self.focus_energy = 1
        self.no_action_consecutive = 0
        # 最近三次no_action的新消息兴趣度记录
        self.recent_interest_records: deque = deque(maxlen=3)

    async def start(self):
        """检查是否需要启动主循环，如果未激活则启动。"""

        # 如果循环已经激活，直接返回
        if self.running:
            logger.debug(f"{self.log_prefix} HeartFChatting 已激活，无需重复启动")
            return

        try:
            # 标记为活动状态，防止重复启动
            self.running = True

            self._loop_task = asyncio.create_task(self._main_chat_loop())
            self._loop_task.add_done_callback(self._handle_loop_completion)
            logger.info(f"{self.log_prefix} HeartFChatting 启动完成")

        except Exception as e:
            # 启动失败时重置状态
            self.running = False
            self._loop_task = None
            logger.error(f"{self.log_prefix} HeartFChatting 启动失败: {e}")
            raise

    def _handle_loop_completion(self, task: asyncio.Task):
        """当 _hfc_loop 任务完成时执行的回调。"""
        try:
            if exception := task.exception():
                logger.error(f"{self.log_prefix} HeartFChatting: 脱离了聊天(异常): {exception}")
                logger.error(traceback.format_exc())  # Log full traceback for exceptions
            else:
                logger.info(f"{self.log_prefix} HeartFChatting: 脱离了聊天 (外部停止)")
        except asyncio.CancelledError:
            logger.info(f"{self.log_prefix} HeartFChatting: 结束了聊天")

    def start_cycle(self) -> Tuple[Dict[str, float], str]:
        self._cycle_counter += 1
        self._current_cycle_detail = CycleDetail(self._cycle_counter)
        self._current_cycle_detail.thinking_id = f"tid{str(round(time.time(), 2))}"
        cycle_timers = {}
        return cycle_timers, self._current_cycle_detail.thinking_id

    def end_cycle(self, loop_info, cycle_timers):
        self._current_cycle_detail.set_loop_info(loop_info)
        self.history_loop.append(self._current_cycle_detail)
        self._current_cycle_detail.timers = cycle_timers
        self._current_cycle_detail.end_time = time.time()

    def print_cycle_info(self, cycle_timers):
        # 记录循环信息和计时器结果
        timer_strings = []
        for name, elapsed in cycle_timers.items():
            formatted_time = f"{elapsed * 1000:.2f}毫秒" if elapsed < 1 else f"{elapsed:.2f}秒"
            timer_strings.append(f"{name}: {formatted_time}")

        # 获取动作类型，兼容新旧格式
        action_type = "未知动作"
        if hasattr(self, "_current_cycle_detail") and self._current_cycle_detail:
            loop_plan_info = self._current_cycle_detail.loop_plan_info
            if isinstance(loop_plan_info, dict):
                action_result = loop_plan_info.get("action_result", {})
                if isinstance(action_result, dict):
                    # 旧格式：action_result是字典
                    action_type = action_result.get("action_type", "未知动作")
                elif isinstance(action_result, list) and action_result:
                    # 新格式：action_result是actions列表
                    # TODO: 把这里写明白
                    action_type = action_result[0].action_type or "未知动作"
            elif isinstance(loop_plan_info, list) and loop_plan_info:
                # 直接是actions列表的情况
                action_type = loop_plan_info[0].get("action_type", "未知动作")

        logger.info(
            f"{self.log_prefix} 第{self._current_cycle_detail.cycle_id}次思考,"
            f"耗时: {self._current_cycle_detail.end_time - self._current_cycle_detail.start_time:.1f}秒, "  # type: ignore
            f"选择动作: {action_type}" + (f"\n详情: {'; '.join(timer_strings)}" if timer_strings else "")
        )

    def _determine_form_type(self) -> None:
        """判断使用哪种形式的no_action"""
        # 如果连续no_action次数少于3次，使用waiting形式
        if self.no_action_consecutive <= 3:
            self.focus_energy = 1
        else:
            # 计算最近三次记录的兴趣度总和
            total_recent_interest = sum(self.recent_interest_records)

            # 计算调整后的阈值
            adjusted_threshold = 1 / self.talk_frequency_control.get_current_talk_frequency()

            logger.info(
                f"{self.log_prefix} 最近三次兴趣度总和: {total_recent_interest:.2f}, 调整后阈值: {adjusted_threshold:.2f}"
            )

            # 如果兴趣度总和小于阈值，进入breaking形式
            if total_recent_interest < adjusted_threshold:
                logger.info(f"{self.log_prefix} 兴趣度不足，进入休息")
                self.focus_energy = random.randint(3, 6)
            else:
                logger.info(f"{self.log_prefix} 兴趣度充足，等待新消息")
                self.focus_energy = 1

    async def _should_process_messages(self, new_message: List["DatabaseMessages"]) -> tuple[bool, float]:
        """
        判断是否应该处理消息

        Args:
            new_message: 新消息列表
            mode: 当前聊天模式

        Returns:
            bool: 是否应该处理消息
        """
        new_message_count = len(new_message)
        talk_frequency = self.talk_frequency_control.get_current_talk_frequency()

        modified_exit_count_threshold = self.focus_energy * 0.5 / talk_frequency
        modified_exit_interest_threshold = 1.5 / talk_frequency
        total_interest = 0.0
        for msg in new_message:
            interest_value = msg.interest_value
            if interest_value is not None and msg.processed_plain_text:
                total_interest += float(interest_value)

        if new_message_count >= modified_exit_count_threshold:
            self.recent_interest_records.append(total_interest)
            logger.info(
                f"{self.log_prefix} 累计消息数量达到{new_message_count}条(>{modified_exit_count_threshold:.1f})，结束等待"
            )
            # logger.info(self.last_read_time)
            # logger.info(new_message)
            return True, total_interest / new_message_count if new_message_count > 0 else 0.0

        # 检查累计兴趣值
        if new_message_count > 0:
            # 只在兴趣值变化时输出log
            if not hasattr(self, "_last_accumulated_interest") or total_interest != self._last_accumulated_interest:
                logger.info(
                    f"{self.log_prefix} 休息中，新消息：{new_message_count}条，累计兴趣值: {total_interest:.2f}, 活跃度: {talk_frequency:.1f}"
                )
                self._last_accumulated_interest = total_interest

            if total_interest >= modified_exit_interest_threshold:
                # 记录兴趣度到列表
                self.recent_interest_records.append(total_interest)
                logger.info(
                    f"{self.log_prefix} 累计兴趣值达到{total_interest:.2f}(>{modified_exit_interest_threshold:.1f})，结束等待"
                )
                return True, total_interest / new_message_count if new_message_count > 0 else 0.0

        # 每10秒输出一次等待状态
        if int(time.time() - self.last_read_time) > 0 and int(time.time() - self.last_read_time) % 15 == 0:
            logger.debug(
                f"{self.log_prefix} 已等待{time.time() - self.last_read_time:.0f}秒，累计{new_message_count}条消息，累计兴趣{total_interest:.1f}，继续等待..."
            )
            await asyncio.sleep(0.5)

        return False, 0.0

    async def _loopbody(self):
        recent_messages_list = message_api.get_messages_by_time_in_chat(
            chat_id=self.stream_id,
            start_time=self.last_read_time,
            end_time=time.time(),
            limit=10,
            limit_mode="latest",
            filter_mai=True,
            filter_command=True,
        )
        # 统一的消息处理逻辑
        should_process, interest_value = await self._should_process_messages(recent_messages_list)

        if should_process:
            self.last_read_time = time.time()
            await self._observe(interest_value=interest_value)

        else:
            # Normal模式：消息数量不足，等待
            await asyncio.sleep(0.5)
            return True
        return True

    async def _send_and_store_reply(
        self,
        response_set,
        action_message: "DatabaseMessages",
        cycle_timers: Dict[str, float],
        thinking_id,
        actions,
        selected_expressions: Optional[List[int]] = None,
    ) -> Tuple[Dict[str, Any], str, Dict[str, float]]:
        with Timer("回复发送", cycle_timers):
            reply_text = await self._send_response(
                reply_set=response_set,
                message_data=action_message,
                selected_expressions=selected_expressions,
            )

        # 获取 platform，如果不存在则从 chat_stream 获取，如果还是 None 则使用默认值
        platform = action_message.chat_info.platform
        if platform is None:
            platform = getattr(self.chat_stream, "platform", "unknown")

        person = Person(platform=platform, user_id=action_message.user_info.user_id)
        person_name = person.person_name
        action_prompt_display = f"你对{person_name}进行了回复：{reply_text}"

        await database_api.store_action_info(
            chat_stream=self.chat_stream,
            action_build_into_prompt=False,
            action_prompt_display=action_prompt_display,
            action_done=True,
            thinking_id=thinking_id,
            action_data={"reply_text": reply_text},
            action_name="reply",
        )

        # 构建循环信息
        loop_info: Dict[str, Any] = {
            "loop_plan_info": {
                "action_result": actions,
            },
            "loop_action_info": {
                "action_taken": True,
                "reply_text": reply_text,
                "command": "",
                "taken_time": time.time(),
            },
        }

        return loop_info, reply_text, cycle_timers

    async def _observe(self, interest_value: float = 0.0) -> bool:
        action_type = "no_action"
        reply_text = ""  # 初始化reply_text变量，避免UnboundLocalError

        # 使用sigmoid函数将interest_value转换为概率
        # 当interest_value为0时，概率接近0（使用Focus模式）
        # 当interest_value很高时，概率接近1（使用Normal模式）
        def calculate_normal_mode_probability(interest_val: float) -> float:
            # 使用sigmoid函数，调整参数使概率分布更合理
            # 当interest_value = 0时，概率约为0.1
            # 当interest_value = 1时，概率约为0.5
            # 当interest_value = 2时，概率约为0.8
            # 当interest_value = 3时，概率约为0.95
            k = 2.0  # 控制曲线陡峭程度
            x0 = 1.0  # 控制曲线中心点
            return 1.0 / (1.0 + math.exp(-k * (interest_val - x0)))

        normal_mode_probability = (
            calculate_normal_mode_probability(interest_value)
            * 2
            * self.talk_frequency_control.get_current_talk_frequency()
        )

        # 根据概率决定使用哪种模式
        if random.random() < normal_mode_probability:
            mode = ChatMode.NORMAL
            logger.info(
                f"{self.log_prefix} 有兴趣({interest_value:.2f})，在{normal_mode_probability * 100:.0f}%概率下选择回复"
            )
        else:
            mode = ChatMode.FOCUS

        # 创建新的循环信息
        cycle_timers, thinking_id = self.start_cycle()

        logger.info(f"{self.log_prefix} 开始第{self._cycle_counter}次思考")

        if s4u_config.enable_s4u:
            await send_typing()

        async with global_prompt_manager.async_message_scope(self.chat_stream.context.get_template_name()):
            await self.relationship_builder.build_relation()
            await self.expression_learner.trigger_learning_for_chat()

            # # 记忆构建：为当前chat_id构建记忆
            # try:
            #     await hippocampus_manager.build_memory_for_chat(self.stream_id)
            # except Exception as e:
            #     logger.error(f"{self.log_prefix} 记忆构建失败: {e}")

            available_actions: Dict[str, ActionInfo] = {}
            if random.random() > self.focus_value_control.get_current_focus_value() and mode == ChatMode.FOCUS:
                # 如果激活度没有激活，并且聊天活跃度低，有可能不进行plan，相当于不在电脑前，不进行认真思考
                action_to_use_info = [
                    ActionPlannerInfo(
                        action_type="no_action",
                        reasoning="专注不足",
                        action_data={},
                    )
                ]
            else:
                # 第一步：动作检查
                with Timer("动作检查", cycle_timers):
                    try:
                        await self.action_modifier.modify_actions()
                        available_actions = self.action_manager.get_using_actions()
                    except Exception as e:
                        logger.error(f"{self.log_prefix} 动作修改失败: {e}")

                # 执行planner
                planner_info = self.action_planner.get_necessary_info()
                

                
                message_list_before_now = get_raw_msg_before_timestamp_with_chat(
                    chat_id=self.stream_id,
                    timestamp=time.time(),
                    limit=int(global_config.chat.max_context_size * 0.6),
                )
                chat_content_block, message_id_list = build_readable_messages_with_id(
                    messages=message_list_before_now,
                    timestamp_mode="normal_no_YMD",
                    read_mark=self.action_planner.last_obs_time_mark,
                    truncate=True,
                    show_actions=True,
                )               
                
                
                prompt_info = await self.action_planner.build_planner_prompt(
                    is_group_chat=planner_info[0],
                    chat_target_info=planner_info[1],
                    # current_available_actions=planner_info[2],
                    chat_content_block=chat_content_block,
                    # actions_before_now_block=actions_before_now_block,
                    message_id_list=message_id_list,
                )

                # 触发 ON_PLAN 事件
                result = await event_manager.trigger_event("on_plan", {
                    "prompt": prompt_info[0],
                    "stream_id": self.chat_stream.stream_id
                })
                if result and not result.all_continue_process():
                    return
                
                with Timer("规划器", cycle_timers):
                    action_to_use_info, _ = await self.action_planner.plan(
                        mode=mode,
                        loop_start_time=self.last_read_time,
                        available_actions=available_actions,
                    )
                    
                    for action in action_to_use_info:
                        print(action.action_type)

            # 3. 并行执行所有动作
            action_tasks = [
                asyncio.create_task(
                    self._execute_action(action, action_to_use_info, thinking_id, available_actions, cycle_timers)
                )
                for action in action_to_use_info
            ]

            # 并行执行所有任务
            results = await asyncio.gather(*action_tasks, return_exceptions=True)

            # 处理执行结果
            reply_loop_info = None
            reply_text_from_reply = ""
            action_success = False
            action_reply_text = ""
            action_command = ""

            for i, result in enumerate(results):
                if isinstance(result, BaseException):
                    logger.error(f"{self.log_prefix} 动作执行异常: {result}")
                    continue

                _cur_action = action_to_use_info[i]
                if result["action_type"] != "reply":
                    action_success = result["success"]
                    action_reply_text = result["reply_text"]
                    action_command = result.get("command", "")
                elif result["action_type"] == "reply":
                    if result["success"]:
                        reply_loop_info = result["loop_info"]
                        reply_text_from_reply = result["reply_text"]
                    else:
                        logger.warning(f"{self.log_prefix} 回复动作执行失败")

            # 构建最终的循环信息
            if reply_loop_info:
                # 如果有回复信息，使用回复的loop_info作为基础
                loop_info = reply_loop_info
                # 更新动作执行信息
                loop_info["loop_action_info"].update(
                    {
                        "action_taken": action_success,
                        "command": action_command,
                        "taken_time": time.time(),
                    }
                )
                reply_text = reply_text_from_reply
            else:
                # 没有回复信息，构建纯动作的loop_info
                loop_info = {
                    "loop_plan_info": {
                        "action_result": action_to_use_info,
                    },
                    "loop_action_info": {
                        "action_taken": action_success,
                        "reply_text": action_reply_text,
                        "command": action_command,
                        "taken_time": time.time(),
                    },
                }
                reply_text = action_reply_text

        if s4u_config.enable_s4u:
            await stop_typing()
            await mai_thinking_manager.get_mai_think(self.stream_id).do_think_after_response(reply_text)

        self.end_cycle(loop_info, cycle_timers)
        self.print_cycle_info(cycle_timers)

        # await self.willing_manager.after_generate_reply_handle(message_data.get("message_id", ""))

        action_type = action_to_use_info[0].action_type if action_to_use_info else "no_action"

        # 管理no_action计数器：当执行了非no_action动作时，重置计数器
        if action_type != "no_action":
            # no_action逻辑已集成到heartFC_chat.py中，直接重置计数器
            self.recent_interest_records.clear()
            self.no_action_consecutive = 0
            logger.debug(f"{self.log_prefix} 执行了{action_type}动作，重置no_action计数器")
            return True

        if action_type == "no_action":
            self.no_action_consecutive += 1
            self._determine_form_type()

        return True

    async def _main_chat_loop(self):
        """主循环，持续进行计划并可能回复消息，直到被外部取消。"""
        try:
            while self.running:
                # 主循环
                success = await self._loopbody()
                await asyncio.sleep(0.1)
                if not success:
                    break
        except asyncio.CancelledError:
            # 设置了关闭标志位后被取消是正常流程
            logger.info(f"{self.log_prefix} 麦麦已关闭聊天")
        except Exception:
            logger.error(f"{self.log_prefix} 麦麦聊天意外错误，将于3s后尝试重新启动")
            print(traceback.format_exc())
            await asyncio.sleep(3)
            self._loop_task = asyncio.create_task(self._main_chat_loop())
        logger.error(f"{self.log_prefix} 结束了当前聊天循环")

    async def _handle_action(
        self,
        action: str,
        reasoning: str,
        action_data: dict,
        cycle_timers: Dict[str, float],
        thinking_id: str,
        action_message: Optional["DatabaseMessages"] = None,
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
                    action_message=action_message,
                )
            except Exception as e:
                logger.error(f"{self.log_prefix} 创建动作处理器时出错: {e}")
                traceback.print_exc()
                return False, "", ""

            if not action_handler:
                logger.warning(f"{self.log_prefix} 未能创建动作处理器: {action}")
                return False, "", ""

            # 处理动作并获取结果
            result = await action_handler.handle_action()
            success, action_text = result
            command = ""

            return success, action_text, command

        except Exception as e:
            logger.error(f"{self.log_prefix} 处理{action}时出错: {e}")
            traceback.print_exc()
            return False, "", ""

    async def _send_response(
        self,
        reply_set,
        message_data: "DatabaseMessages",
        selected_expressions: Optional[List[int]] = None,
    ) -> str:
        new_message_count = message_api.count_new_messages(
            chat_id=self.chat_stream.stream_id, start_time=self.last_read_time, end_time=time.time()
        )

        need_reply = new_message_count >= random.randint(2, 4)

        if need_reply:
            logger.info(f"{self.log_prefix} 从思考到回复，共有{new_message_count}条新消息，使用引用回复")

        reply_text = ""
        first_replied = False
        for reply_seg in reply_set:
            data = reply_seg[1]
            if not first_replied:
                await send_api.text_to_stream(
                    text=data,
                    stream_id=self.chat_stream.stream_id,
                    reply_message=message_data,
                    set_reply=need_reply,
                    typing=False,
                    selected_expressions=selected_expressions,
                )
                first_replied = True
            else:
                await send_api.text_to_stream(
                    text=data,
                    stream_id=self.chat_stream.stream_id,
                    reply_message=message_data,
                    set_reply=False,
                    typing=True,
                    selected_expressions=selected_expressions,
                )
            reply_text += data

        return reply_text

    async def _execute_action(
        self,
        action_planner_info: ActionPlannerInfo,
        chosen_action_plan_infos: List[ActionPlannerInfo],
        thinking_id: str,
        available_actions: Dict[str, ActionInfo],
        cycle_timers: Dict[str, float],
    ):
        """执行单个动作的通用函数"""
        try:
            if action_planner_info.action_type == "no_action":
                # 直接处理no_action逻辑，不再通过动作系统
                reason = action_planner_info.reasoning or "选择不回复"
                logger.info(f"{self.log_prefix} 选择不回复，原因: {reason}")

                # 存储no_action信息到数据库
                await database_api.store_action_info(
                    chat_stream=self.chat_stream,
                    action_build_into_prompt=False,
                    action_prompt_display=reason,
                    action_done=True,
                    thinking_id=thinking_id,
                    action_data={"reason": reason},
                    action_name="no_action",
                )

                return {"action_type": "no_action", "success": True, "reply_text": "", "command": ""}
            elif action_planner_info.action_type != "reply":
                # 执行普通动作
                with Timer("动作执行", cycle_timers):
                    success, reply_text, command = await self._handle_action(
                        action_planner_info.action_type,
                        action_planner_info.reasoning or "",
                        action_planner_info.action_data or {},
                        cycle_timers,
                        thinking_id,
                        action_planner_info.action_message,
                    )
                return {
                    "action_type": action_planner_info.action_type,
                    "success": success,
                    "reply_text": reply_text,
                    "command": command,
                }
            else:
                try:
                    success, llm_response = await generator_api.generate_reply(
                        chat_stream=self.chat_stream,
                        reply_message=action_planner_info.action_message,
                        available_actions=available_actions,
                        chosen_actions=chosen_action_plan_infos,
                        reply_reason=action_planner_info.reasoning or "",
                        enable_tool=global_config.tool.enable_tool,
                        request_type="replyer",
                        from_plugin=False,
                    )

                    if not success or not llm_response or not llm_response.reply_set:
                        if action_planner_info.action_message:
                            logger.info(f"对 {action_planner_info.action_message.processed_plain_text} 的回复生成失败")
                        else:
                            logger.info("回复生成失败")
                        return {"action_type": "reply", "success": False, "reply_text": "", "loop_info": None}

                except asyncio.CancelledError:
                    logger.debug(f"{self.log_prefix} 并行执行：回复生成任务已被取消")
                    return {"action_type": "reply", "success": False, "reply_text": "", "loop_info": None}
                response_set = llm_response.reply_set
                selected_expressions = llm_response.selected_expressions
                loop_info, reply_text, _ = await self._send_and_store_reply(
                    response_set=response_set,
                    action_message=action_planner_info.action_message,  # type: ignore
                    cycle_timers=cycle_timers,
                    thinking_id=thinking_id,
                    actions=chosen_action_plan_infos,
                    selected_expressions=selected_expressions,
                )
                return {
                    "action_type": "reply",
                    "success": True,
                    "reply_text": reply_text,
                    "loop_info": loop_info,
                }
        except Exception as e:
            logger.error(f"{self.log_prefix} 执行动作时出错: {e}")
            logger.error(f"{self.log_prefix} 错误信息: {traceback.format_exc()}")
            return {
                "action_type": action_planner_info.action_type,
                "success": False,
                "reply_text": "",
                "loop_info": None,
                "error": str(e),
            }
