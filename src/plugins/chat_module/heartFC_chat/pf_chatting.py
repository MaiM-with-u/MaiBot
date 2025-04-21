import asyncio
import time
import traceback
from typing import List, Optional, Dict, Any, TYPE_CHECKING
import json
from src.plugins.chat.message import MessageRecv, BaseMessageInfo, MessageThinking, MessageSending
from src.plugins.chat.message import MessageSet, Seg  # Local import needed after move
from src.plugins.chat.chat_stream import ChatStream
from src.plugins.chat.message import UserInfo
from src.heart_flow.heartflow import heartflow, SubHeartflow
from src.plugins.chat.chat_stream import chat_manager
from src.common.logger import get_module_logger, LogConfig, PFC_STYLE_CONFIG  # 引入 DEFAULT_CONFIG
from src.plugins.models.utils_model import LLMRequest
from src.config.config import global_config
from src.plugins.chat.utils_image import image_path_to_base64  # Local import needed after move
from src.plugins.utils.timer_calculater import Timer  # <--- Import Timer

# 定义日志配置 (使用 loguru 格式)
interest_log_config = LogConfig(
    console_format=PFC_STYLE_CONFIG["console_format"],  # 使用默认控制台格式
    file_format=PFC_STYLE_CONFIG["file_format"],  # 使用默认文件格式
)
logger = get_module_logger("PFCLoop", config=interest_log_config)  # Logger Name Changed


# Forward declaration for type hinting
if TYPE_CHECKING:
    from .heartFC_controler import HeartFCController

PLANNER_TOOL_DEFINITION = [
    {
        "type": "function",
        "function": {
            "name": "decide_reply_action",
            "description": "根据当前聊天内容和上下文，决定机器人是否应该回复以及如何回复。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["no_reply", "text_reply", "emoji_reply"],
                        "description": "决定采取的行动：'no_reply'(不回复), 'text_reply'(文本回复, 可选附带表情) 或 'emoji_reply'(仅表情回复)。",
                    },
                    "reasoning": {"type": "string", "description": "做出此决定的简要理由。"},
                    "emoji_query": {
                        "type": "string",
                        "description": "如果行动是'emoji_reply'，指定表情的主题或概念。如果行动是'text_reply'且希望在文本后追加表情，也在此指定表情主题。",
                    },
                },
                "required": ["action", "reasoning"],
            },
        },
    }
]


class PFChatting:
    """
    管理一个连续的Plan-Filter-Check (现在改为Plan-Replier-Sender)循环
    用于在特定聊天流中生成回复，由计时器控制。
    只要计时器>0，循环就会继续。
    """

    def __init__(self, chat_id: str, heartfc_controller_instance: "HeartFCController"):
        """
        初始化PFChatting实例。

        Args:
            chat_id: The identifier for the chat stream (e.g., stream_id).
            heartfc_controller_instance: 访问共享资源和方法的主HeartFC_Controller实例。
        """
        self.heartfc_controller = heartfc_controller_instance  # Store the controller instance
        self.stream_id: str = chat_id
        self.chat_stream: Optional[ChatStream] = None
        self.sub_hf: Optional[SubHeartflow] = None
        self._initialized = False
        self._init_lock = asyncio.Lock()  # Ensure initialization happens only once
        self._processing_lock = asyncio.Lock()  # 确保只有一个 Plan-Replier-Sender 周期在运行
        self._timer_lock = asyncio.Lock()  # 用于安全更新计时器

        # Access LLM config through the controller
        self.planner_llm = LLMRequest(
            model=global_config.llm_normal,
            temperature=global_config.llm_normal["temp"],
            max_tokens=1000,
            request_type="action_planning",
        )

        # Internal state for loop control
        self._loop_timer: float = 0.0  # Remaining time for the loop in seconds
        self._loop_active: bool = False  # Is the loop currently running?
        self._loop_task: Optional[asyncio.Task] = None  # Stores the main loop task
        self._trigger_count_this_activation: int = 0  # Counts triggers within an active period
        self._initial_duration: float = 60.0  # 首次触发增加的时间
        self._last_added_duration: float = self._initial_duration  # <--- 新增：存储上次增加的时间

    def _get_log_prefix(self) -> str:
        """获取日志前缀，包含可读的流名称"""
        stream_name = chat_manager.get_stream_name(self.stream_id) or self.stream_id
        return f"[{stream_name}]"

    async def _initialize(self) -> bool:
        """
        懒初始化以使用提供的标识符解析chat_stream和sub_hf。
        确保实例已准备好处理触发器。
        """
        async with self._init_lock:
            if self._initialized:
                return True
            log_prefix = self._get_log_prefix()  # 获取前缀
            try:
                self.chat_stream = chat_manager.get_stream(self.stream_id)

                if not self.chat_stream:
                    logger.error(f"{log_prefix} 获取ChatStream失败。")
                    return False

                self.sub_hf = heartflow.get_subheartflow(self.stream_id)
                if not self.sub_hf:
                    logger.warning(f"{log_prefix} 获取SubHeartflow失败。一些功能可能受限。")

                self._initialized = True
                logger.info(f"麦麦感觉到了，激发了PFChatting{log_prefix} 初始化成功。")
                return True
            except Exception as e:
                logger.error(f"{log_prefix} 初始化失败: {e}")
                logger.error(traceback.format_exc())
                return False

    async def add_time(self):
        """
        为麦麦添加时间，麦麦有兴趣时，时间增加。
        """
        log_prefix = self._get_log_prefix()
        if not self._initialized:
            if not await self._initialize():
                logger.error(f"{log_prefix} 无法添加时间: 未初始化。")
                return

        async with self._timer_lock:
            duration_to_add: float = 0.0

            if not self._loop_active:  # First trigger for this activation cycle
                duration_to_add = self._initial_duration  # 使用初始值
                self._last_added_duration = duration_to_add  # 更新上次增加的值
                self._trigger_count_this_activation = 1  # Start counting
                logger.info(
                    f"{log_prefix} 麦麦有兴趣！ #{self._trigger_count_this_activation}. 麦麦打算聊： {duration_to_add:.2f}s."
                )
            else:  # Loop is already active, apply 50% reduction
                self._trigger_count_this_activation += 1
                duration_to_add = self._last_added_duration * 0.5
                if duration_to_add < 1.5:
                    duration_to_add = 1.5
                # Update _last_added_duration only if it's >= 0.5 to prevent it from becoming too small
                self._last_added_duration = duration_to_add
                logger.info(
                    f"{log_prefix} 麦麦兴趣增加！ #{self._trigger_count_this_activation}. 想继续聊： {duration_to_add:.2f}s, 麦麦还能聊： {self._loop_timer:.1f}s."
                )

            # 添加计算出的时间
            new_timer_value = self._loop_timer + duration_to_add
            # Add max timer duration limit? e.g., max(0, min(new_timer_value, 300))
            self._loop_timer = max(0, new_timer_value)
            # Log less frequently, e.g., every 10 seconds or significant change?
            # if self._trigger_count_this_activation % 5 == 0:
            # logger.info(f"{log_prefix} 麦麦现在想聊{self._loop_timer:.1f}秒")

            # Start the loop if it wasn't active and timer is positive
            if not self._loop_active and self._loop_timer > 0:
                self._loop_active = True
                if self._loop_task and not self._loop_task.done():
                    logger.warning(f"{log_prefix} 发现意外的循环任务正在进行。取消它。")
                    self._loop_task.cancel()

                self._loop_task = asyncio.create_task(self._run_pf_loop())
                self._loop_task.add_done_callback(self._handle_loop_completion)
            elif self._loop_active:
                logger.trace(f"{log_prefix} 循环已经激活。计时器延长。")

    def _handle_loop_completion(self, task: asyncio.Task):
        """当 _run_pf_loop 任务完成时执行的回调。"""
        log_prefix = self._get_log_prefix()
        try:
            exception = task.exception()
            if exception:
                logger.error(f"{log_prefix} PFChatting: 麦麦脱离了聊天(异常): {exception}")
                logger.error(traceback.format_exc())  # Log full traceback for exceptions
            else:
                logger.debug(f"{log_prefix} PFChatting: 麦麦脱离了聊天 (正常完成)")
        except asyncio.CancelledError:
            logger.info(f"{log_prefix} PFChatting: 麦麦脱离了聊天(任务取消)")
        finally:
            self._loop_active = False
            self._loop_task = None
            self._last_added_duration = self._initial_duration
            self._trigger_count_this_activation = 0
            if self._processing_lock.locked():
                logger.warning(f"{log_prefix} PFChatting: 处理锁在循环结束时仍被锁定，强制释放。")
                self._processing_lock.release()
            # Remove instance from controller's dict? Only if it's truly done.
            # Consider if loop can be restarted vs instance destroyed.
            # asyncio.create_task(self.heartfc_controller._remove_pf_chatting_instance(self.stream_id)) # Example cleanup

    async def _run_pf_loop(self):
        """
        主循环，当计时器>0时持续进行计划并可能回复消息
        管理每个循环周期的处理锁
        """
        log_prefix = self._get_log_prefix()
        logger.info(f"{log_prefix} PFChatting: 麦麦打算好好聊聊 (定时器: {self._loop_timer:.1f}s)")
        try:
            thinking_id = ""
            while True:
                cycle_timers = {}  # <--- Initialize timers dict for this cycle

                if self.heartfc_controller.MessageManager().check_if_sending_message_exist(self.stream_id, thinking_id):
                    # logger.info(f"{log_prefix} PFChatting: 11111111111111111111111111111111麦麦还在发消息，等会再规划")
                    await asyncio.sleep(1)
                    continue
                else:
                    # logger.info(f"{log_prefix} PFChatting: 11111111111111111111111111111111麦麦不发消息了，开始规划")
                    pass

                async with self._timer_lock:
                    current_timer = self._loop_timer
                    if current_timer <= 0:
                        logger.info(
                            f"{log_prefix} PFChatting: 聊太久了，麦麦打算休息一下 (计时器为 {current_timer:.1f}s)。退出PFChatting。"
                        )
                        break

                # 记录循环周期开始时间，用于计时和休眠计算
                loop_cycle_start_time = time.monotonic()
                action_taken_this_cycle = False
                acquired_lock = False
                planner_start_db_time = 0.0  # 初始化

                try:
                    with Timer("Total Cycle", cycle_timers) as _total_timer:  # <--- Start total cycle timer
                        # Use try_acquire pattern or timeout?
                        await self._processing_lock.acquire()
                        acquired_lock = True
                        # logger.debug(f"{log_prefix} PFChatting: 循环获取到处理锁")

                        # 在规划前记录数据库时间戳
                        planner_start_db_time = time.time()

                        # --- Planner --- #
                        planner_result = {}
                        with Timer("Planner", cycle_timers):  # <--- Start Planner timer
                            planner_result = await self._planner()
                        action = planner_result.get("action", "error")
                        reasoning = planner_result.get("reasoning", "Planner did not provide reasoning.")
                        emoji_query = planner_result.get("emoji_query", "")
                        # current_mind = planner_result.get("current_mind", "[Mind unavailable]")
                        # send_emoji_from_tools = planner_result.get("send_emoji_from_tools", "") # Emoji from tools
                        observed_messages = planner_result.get("observed_messages", [])
                        llm_error = planner_result.get("llm_error", False)

                        if llm_error:
                            logger.error(f"{log_prefix} Planner LLM 失败，跳过本周期回复尝试。理由: {reasoning}")
                            # Optionally add a longer sleep?
                            action_taken_this_cycle = False  # Ensure no action is counted
                            # Continue to timer decrement and sleep

                        elif action == "text_reply":
                            logger.info(f"{log_prefix} PFChatting: 麦麦决定回复文本. 理由: {reasoning}")
                            action_taken_this_cycle = True
                            anchor_message = await self._get_anchor_message(observed_messages)
                            if not anchor_message:
                                logger.error(f"{log_prefix} 循环: 无法获取锚点消息用于回复. 跳过周期.")
                            else:
                                # --- Create Thinking Message (Moved) ---
                                thinking_id = await self._create_thinking_message(anchor_message)
                                if not thinking_id:
                                    logger.error(f"{log_prefix} 循环: 无法创建思考ID. 跳过周期.")
                                else:
                                    replier_result = None
                                    try:
                                        # --- Replier Work --- #
                                        with Timer("Replier", cycle_timers):  # <--- Start Replier timer
                                            replier_result = await self._replier_work(
                                                anchor_message=anchor_message,
                                                thinking_id=thinking_id,
                                                reason=reasoning,
                                            )
                                    except Exception as e_replier:
                                        logger.error(f"{log_prefix} 循环: 回复器工作失败: {e_replier}")
                                        self._cleanup_thinking_message(thinking_id)

                                    if replier_result:
                                        # --- Sender Work --- #
                                        try:
                                            with Timer("Sender", cycle_timers):  # <--- Start Sender timer
                                                await self._sender(
                                                    thinking_id=thinking_id,
                                                    anchor_message=anchor_message,
                                                    response_set=replier_result,
                                                    send_emoji=emoji_query,
                                                )
                                            # logger.info(f"{log_prefix} 循环: 发送器完成成功.")
                                        except Exception as e_sender:
                                            logger.error(f"{log_prefix} 循环: 发送器失败: {e_sender}")
                                            # _sender should handle cleanup, but double check
                                            # self._cleanup_thinking_message(thinking_id)
                                    else:
                                        logger.warning(f"{log_prefix} 循环: 回复器未产生结果. 跳过发送.")
                                        self._cleanup_thinking_message(thinking_id)
                        elif action == "emoji_reply":
                            logger.info(
                                f"{log_prefix} PFChatting: 麦麦决定回复表情 ('{emoji_query}'). 理由: {reasoning}"
                            )
                            action_taken_this_cycle = True
                            anchor = await self._get_anchor_message(observed_messages)
                            if anchor:
                                try:
                                    # --- Handle Emoji (Moved) --- #
                                    with Timer("Emoji Handler", cycle_timers):  # <--- Start Emoji timer
                                        await self._handle_emoji(anchor, [], emoji_query)
                                except Exception as e_emoji:
                                    logger.error(f"{log_prefix} 循环: 发送表情失败: {e_emoji}")
                            else:
                                logger.warning(f"{log_prefix} 循环: 无法发送表情, 无法获取锚点.")
                            action_taken_this_cycle = True  # 即使发送失败，Planner 也决策了动作

                        elif action == "no_reply":
                            logger.info(f"{log_prefix} PFChatting: 麦麦决定不回复. 原因: {reasoning}")
                            action_taken_this_cycle = False  # 标记为未执行动作
                            # --- 新增：等待新消息 ---
                            logger.debug(f"{log_prefix} PFChatting: 开始等待新消息 (自 {planner_start_db_time})...")
                            observation = None
                            if self.sub_hf:
                                observation = self.sub_hf._get_primary_observation()

                            if observation:
                                with Timer("Wait New Msg", cycle_timers):  # <--- Start Wait timer
                                    wait_start_time = time.monotonic()
                                    while True:
                                        # 检查计时器是否耗尽
                                        async with self._timer_lock:
                                            if self._loop_timer <= 0:
                                                logger.info(f"{log_prefix} PFChatting: 等待新消息时计时器耗尽。")
                                                break  # 计时器耗尽，退出等待

                                        # 检查是否有新消息
                                        has_new = await observation.has_new_messages_since(planner_start_db_time)
                                        if has_new:
                                            logger.info(f"{log_prefix} PFChatting: 检测到新消息，结束等待。")
                                            break  # 收到新消息，退出等待

                                        # 检查等待是否超时（例如，防止无限等待）
                                        if time.monotonic() - wait_start_time > 60:  # 等待60秒示例
                                            logger.warning(f"{log_prefix} PFChatting: 等待新消息超时（60秒）。")
                                            break  # 超时退出

                                        # 等待一段时间再检查
                                        try:
                                            await asyncio.sleep(1.5)  # 检查间隔
                                        except asyncio.CancelledError:
                                            logger.info(f"{log_prefix} 等待新消息的 sleep 被中断。")
                                            raise  # 重新抛出取消错误，以便外层循环处理
                            else:
                                logger.warning(f"{log_prefix} PFChatting: 无法获取 Observation 实例，无法等待新消息。")
                            # --- 等待结束 ---

                        elif action == "error":  # Action specifically set to error by planner
                            logger.error(f"{log_prefix} PFChatting: Planner返回错误状态. 原因: {reasoning}")
                            action_taken_this_cycle = False

                        else:  # Unknown action from planner
                            logger.warning(
                                f"{log_prefix} PFChatting: Planner返回未知动作 '{action}'. 原因: {reasoning}"
                            )
                            action_taken_this_cycle = False

                    # --- Print Timer Results --- #
                    if cycle_timers:  # 先检查cycle_timers是否非空
                        timer_strings = []
                        for name, elapsed in cycle_timers.items():
                            # 直接格式化存储在字典中的浮点数 elapsed
                            formatted_time = f"{elapsed * 1000:.2f}毫秒" if elapsed < 1 else f"{elapsed:.2f}秒"
                            timer_strings.append(f"{name}: {formatted_time}")

                        if timer_strings:  # 如果有有效计时器数据才打印
                            logger.debug(
                                f"{log_prefix} test testtesttesttesttesttesttesttesttesttest Cycle Timers: {'; '.join(timer_strings)}"
                            )

                    # --- Timer Decrement --- #
                    cycle_duration = time.monotonic() - loop_cycle_start_time

                except Exception as e_cycle:
                    logger.error(f"{log_prefix} 循环周期执行时发生错误: {e_cycle}")
                    logger.error(traceback.format_exc())
                    if acquired_lock and self._processing_lock.locked():
                        self._processing_lock.release()
                        acquired_lock = False
                        logger.warning(f"{log_prefix} 由于循环周期中的错误释放了处理锁.")

                finally:
                    if acquired_lock:
                        self._processing_lock.release()
                        logger.trace(f"{log_prefix} 循环释放了处理锁.")

                async with self._timer_lock:
                    self._loop_timer -= cycle_duration
                    # Log timer decrement less aggressively
                    if cycle_duration > 0.1 or not action_taken_this_cycle:
                        logger.debug(
                            f"{log_prefix} PFChatting: 周期耗时 {cycle_duration:.2f}s. 剩余时间: {self._loop_timer:.1f}s."
                        )

                # --- Delay --- #
                try:
                    sleep_duration = 0.0
                    if not action_taken_this_cycle and cycle_duration < 1.5:
                        sleep_duration = 1.5 - cycle_duration
                    elif cycle_duration < 0.2:
                        sleep_duration = 0.2

                    if sleep_duration > 0:
                        # logger.debug(f"{log_prefix} Sleeping for {sleep_duration:.2f}s")
                        await asyncio.sleep(sleep_duration)

                except asyncio.CancelledError:
                    logger.info(f"{log_prefix} Sleep interrupted, loop likely cancelling.")
                    break

        except asyncio.CancelledError:
            logger.info(f"{log_prefix} PFChatting: 麦麦的聊天主循环被取消了")
        except Exception as e_loop_outer:
            logger.error(f"{log_prefix} PFChatting: 麦麦的聊天主循环意外出错: {e_loop_outer}")
            logger.error(traceback.format_exc())
        finally:
            # State reset is primarily handled by _handle_loop_completion callback
            logger.info(f"{log_prefix} PFChatting: 麦麦的聊天主循环结束。")

    async def _planner(self) -> Dict[str, Any]:
        """
        规划器 (Planner): 使用LLM根据上下文决定是否和如何回复。
        """
        log_prefix = self._get_log_prefix()
        observed_messages: List[dict] = []
        tool_result_info = {}
        get_mid_memory_id = []
        # send_emoji_from_tools = "" # Emoji suggested by tools
        current_mind: Optional[str] = None
        llm_error = False  # Flag for LLM failure

        try:
            observation = self.sub_hf._get_primary_observation()
            await observation.observe()
            observed_messages = observation.talking_message
            observed_messages_str = observation.talking_message_str
        except Exception as e:
            logger.error(f"{log_prefix}[Planner] 获取观察信息时出错: {e}")
        # --- 结束获取观察信息 --- #

        # --- (Moved from _replier_work) 1. 思考前使用工具 --- #
        try:
            # Access tool_user via controller
            tool_result = await self.heartfc_controller.tool_user.use_tool(
                message_txt=observed_messages_str, sub_heartflow=self.sub_hf
            )
            if tool_result.get("used_tools", False):
                tool_result_info = tool_result.get("structured_info", {})
                logger.debug(f"{log_prefix}[Planner] 规划前工具结果: {tool_result_info}")

                get_mid_memory_id = [
                    mem["content"] for mem in tool_result_info.get("mid_chat_mem", []) if "content" in mem
                ]

        except Exception as e_tool:
            logger.error(f"{log_prefix}[Planner] 规划前工具使用失败: {e_tool}")
        # --- 结束工具使用 --- #

        # --- (Moved from _replier_work) 2. SubHeartflow 思考 --- #
        try:
            current_mind, _past_mind = await self.sub_hf.do_thinking_before_reply(
                extra_info=tool_result_info,
                obs_id=get_mid_memory_id,
            )
            # logger.debug(f"{log_prefix}[Planner] SubHF Mind: {current_mind}")
        except Exception as e_subhf:
            logger.error(f"{log_prefix}[Planner] SubHeartflow 思考失败: {e_subhf}")
            current_mind = "[思考时出错]"
        # --- 结束 SubHeartflow 思考 --- #

        # --- 使用 LLM 进行决策 --- #
        action = "no_reply"  # Default action
        emoji_query = ""  # Default emoji query (used if action is emoji_reply or text_reply with emoji)
        reasoning = "默认决策或获取决策失败"

        try:
            prompt = await self._build_planner_prompt(observed_messages_str, current_mind)
            payload = {
                "model": self.planner_llm.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "tools": PLANNER_TOOL_DEFINITION,
                "tool_choice": {"type": "function", "function": {"name": "decide_reply_action"}},
            }

            response = await self.planner_llm._execute_request(
                endpoint="/chat/completions", payload=payload, prompt=prompt
            )

            if len(response) == 3:
                _, _, tool_calls = response
                if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
                    tool_call = tool_calls[0]
                    if (
                        tool_call.get("type") == "function"
                        and tool_call.get("function", {}).get("name") == "decide_reply_action"
                    ):
                        try:
                            arguments = json.loads(tool_call["function"]["arguments"])
                            action = arguments.get("action", "no_reply")
                            reasoning = arguments.get("reasoning", "未提供理由")
                            # Planner explicitly provides emoji query if action is emoji_reply or text_reply wants emoji
                            emoji_query = arguments.get("emoji_query", "")
                            logger.debug(
                                f"{log_prefix}[Planner] LLM Prompt: {prompt}\n决策: {action}, 理由: {reasoning}, EmojiQuery: '{emoji_query}'"
                            )
                        except json.JSONDecodeError as json_e:
                            logger.error(
                                f"{log_prefix}[Planner] 解析工具参数失败: {json_e}. Args: {tool_call['function'].get('arguments')}"
                            )
                            action = "error"
                            reasoning = "工具参数解析失败"
                            llm_error = True
                        except Exception as parse_e:
                            logger.error(f"{log_prefix}[Planner] 处理工具参数时出错: {parse_e}")
                            action = "error"
                            reasoning = "处理工具参数时出错"
                            llm_error = True
                    else:
                        logger.warning(
                            f"{log_prefix}[Planner] LLM 未按预期调用 'decide_reply_action' 工具。Tool calls: {tool_calls}"
                        )
                        action = "error"
                        reasoning = "LLM未调用预期工具"
                        llm_error = True
                else:
                    logger.warning(f"{log_prefix}[Planner] LLM 响应中未包含有效的工具调用。Tool calls: {tool_calls}")
                    action = "error"
                    reasoning = "LLM响应无工具调用"
                    llm_error = True
            else:
                logger.warning(f"{log_prefix}[Planner] LLM 未返回预期的工具调用响应。Response parts: {len(response)}")
                action = "error"
                reasoning = "LLM响应格式错误"
                llm_error = True

        except Exception as llm_e:
            logger.error(f"{log_prefix}[Planner] Planner LLM 调用失败: {llm_e}")
            # logger.error(traceback.format_exc()) # Maybe too verbose for loop?
            action = "error"
            reasoning = f"LLM 调用失败: {llm_e}"
            llm_error = True
        # --- 结束 LLM 决策 --- #

        return {
            "action": action,
            "reasoning": reasoning,
            "emoji_query": emoji_query,  # Explicit query from Planner/LLM
            "current_mind": current_mind,
            # "send_emoji_from_tools": send_emoji_from_tools, # Emoji suggested by tools (used as fallback)
            "observed_messages": observed_messages,
            "llm_error": llm_error,
        }

    async def _get_anchor_message(self, observed_messages: List[dict]) -> Optional[MessageRecv]:
        """
        重构观察到的最后一条消息作为回复的锚点，
        如果重构失败或观察为空，则创建一个占位符。
        """

        try:
            last_msg_dict = None
            if observed_messages:
                last_msg_dict = observed_messages[-1]

            if last_msg_dict:
                try:
                    # anchor_message = MessageRecv(last_msg_dict, chat_stream=self.chat_stream)
                    anchor_message = MessageRecv(last_msg_dict)  # 移除 chat_stream 参数
                    anchor_message.update_chat_stream(self.chat_stream)  # 添加 update_chat_stream 调用
                    if not (
                        anchor_message
                        and anchor_message.message_info
                        and anchor_message.message_info.message_id
                        and anchor_message.message_info.user_info
                    ):
                        raise ValueError("重构的 MessageRecv 缺少必要信息.")
                    # logger.debug(f"{self._get_log_prefix()} 重构的锚点消息: ID={anchor_message.message_info.message_id}")
                    return anchor_message
                except Exception as e_reconstruct:
                    logger.warning(
                        f"{self._get_log_prefix()} 从观察到的消息重构 MessageRecv 失败: {e_reconstruct}. 创建占位符."
                    )
            # else:
            # logger.warning(f"{self._get_log_prefix()} observed_messages 为空. 创建占位符锚点消息.")

            # --- Create Placeholder --- #
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
            logger.info(
                f"{self._get_log_prefix()} Created placeholder anchor message: ID={anchor_message.message_info.message_id}"
            )
            return anchor_message

        except Exception as e:
            logger.error(f"{self._get_log_prefix()} Error getting/creating anchor message: {e}")
            logger.error(traceback.format_exc())
            return None

    def _cleanup_thinking_message(self, thinking_id: str):
        """Safely removes the thinking message."""
        log_prefix = self._get_log_prefix()
        try:
            # Access MessageManager via controller
            container = self.heartfc_controller.MessageManager().get_container(self.stream_id)
            container.remove_message(thinking_id, msg_type=MessageThinking)
            logger.debug(f"{log_prefix} Cleaned up thinking message {thinking_id}.")
        except Exception as e:
            logger.error(f"{log_prefix} Error cleaning up thinking message {thinking_id}: {e}")

    # --- 发送器 (Sender) --- #
    async def _sender(
        self,
        thinking_id: str,
        anchor_message: MessageRecv,
        response_set: List[str],
        send_emoji: str,  # Emoji query decided by planner or tools
    ):
        """
        发送器 (Sender): 使用本类的方法发送生成的回复。
        处理相关的操作，如发送表情和更新关系。
        """
        log_prefix = self._get_log_prefix()

        first_bot_msg: Optional[MessageSending] = None
        # 尝试发送回复消息
        first_bot_msg = await self._send_response_messages(anchor_message, response_set, thinking_id)
        if first_bot_msg:
            # --- 处理关联表情(如果指定) --- #
            if send_emoji:
                logger.info(f"{log_prefix}[Sender-{thinking_id}] 正在发送关联表情: '{send_emoji}'")
                # 优先使用first_bot_msg作为锚点，否则回退到原始锚点
                emoji_anchor = first_bot_msg if first_bot_msg else anchor_message
                await self._handle_emoji(emoji_anchor, response_set, send_emoji)

        else:
            # logger.warning(f"{log_prefix}[Sender-{thinking_id}] 发送回复失败(_send_response_messages返回None)。思考消息{thinking_id}可能已被移除。")
            # 无需清理，因为_send_response_messages返回None意味着已处理/已删除
            raise RuntimeError("发送回复失败，_send_response_messages返回None")

    async def shutdown(self):
        """
        Gracefully shuts down the PFChatting instance by cancelling the active loop task.
        """
        log_prefix = self._get_log_prefix()
        logger.info(f"{log_prefix} Shutting down PFChatting...")
        if self._loop_task and not self._loop_task.done():
            logger.info(f"{log_prefix} Cancelling active PF loop task.")
            self._loop_task.cancel()
            try:
                await asyncio.wait_for(self._loop_task, timeout=1.0)  # Shorter timeout?
            except asyncio.CancelledError:
                logger.info(f"{log_prefix} PF loop task cancelled successfully.")
            except asyncio.TimeoutError:
                logger.warning(f"{log_prefix} Timeout waiting for PF loop task cancellation.")
            except Exception as e:
                logger.error(f"{log_prefix} Error during loop task cancellation: {e}")
        else:
            logger.info(f"{log_prefix} No active PF loop task found to cancel.")

        self._loop_active = False
        self._loop_task = None
        if self._processing_lock.locked():
            logger.warning(f"{log_prefix} Releasing processing lock during shutdown.")
            self._processing_lock.release()
        logger.info(f"{log_prefix} PFChatting shutdown complete.")

    async def _build_planner_prompt(self, observed_messages_str: str, current_mind: Optional[str]) -> str:
        """构建 Planner LLM 的提示词"""

        prompt = f"你的名字是 {global_config.BOT_NICKNAME}。是一个女大二学生，正在QQ聊天，正在决定是否以及如何回应当前的聊天。\n"

        if observed_messages_str:
            prompt += "观察到的最新聊天内容如下 (最近的消息在最后)：\n---\n"
            prompt += observed_messages_str
            prompt += "\n---"
        else:
            prompt += "当前没有观察到新的聊天内容。\n"

        prompt += "\n看了以上内容，你产生的内心想法是："
        if current_mind:
            prompt += f"\n---\n{current_mind}\n---\n\n"
        else:
            prompt += " [没有特别的想法] \n\n"

        prompt += (
            "请结合你的内心想法和观察到的聊天内容，分析情况并使用 'decide_reply_action' 工具来决定你的最终行动。\n"
            "决策依据：\n"
            "1. 如果聊天内容无聊、与你无关、或者你的内心想法认为不适合回复（例如在讨论你不懂或不感兴趣的话题），选择 'no_reply'。\n"
            "2. 如果聊天内容值得回应，且适合用文字表达（参考你的内心想法），选择 'text_reply'。如果你有情绪想表达，想在文字后追加一个表达情绪的表情，请同时提供 'emoji_query' (例如：'开心的'、'惊讶的')。\n"
            "3. 如果聊天内容或你的内心想法适合用一个表情来回应（例如表示赞同、惊讶、无语等），选择 'emoji_reply' 并提供表情主题 'emoji_query'。\n"
            "4. 如果最后一条消息是你自己发的，并且之后没有人回复你，通常选择 'no_reply'，除非有特殊原因需要追问。\n"
            "5. 除非大家都在这么做，或者有特殊理由，否则不要重复别人刚刚说过的话或简单附和。\n"
            "6. 表情包是用来表达情绪的，不要直接回复或评价别人的表情包，而是根据对话内容和情绪选择是否用表情回应。\n"
            "7. 如果观察到的内容只有你自己的发言，选择 'no_reply'。\n"
            "8. 不要回复你自己的话，不要把自己的话当做别人说的。\n"
            "必须调用 'decide_reply_action' 工具并提供 'action' 和 'reasoning'。如果选择了 'emoji_reply' 或者选择了 'text_reply' 并想追加表情，则必须提供 'emoji_query'。"
        )

        return prompt

    # --- 回复器 (Replier) 的定义 --- #
    async def _replier_work(
        self,
        reason: str,
        anchor_message: MessageRecv,
        thinking_id: str,
    ) -> Optional[List[str]]:
        """
        回复器 (Replier): 核心逻辑用于生成回复。
        """
        log_prefix = self._get_log_prefix()
        response_set: Optional[List[str]] = None
        try:
            # --- Generate Response with LLM --- #
            # Access gpt instance via controller
            gpt_instance = self.heartfc_controller.gpt
            logger.debug(f"{log_prefix}[Replier-{thinking_id}] Calling LLM to generate response...")

            # Ensure generate_response has access to current_mind if it's crucial context
            response_set = await gpt_instance.generate_response(
                reason,
                anchor_message,  # Pass anchor_message positionally (matches 'message' parameter)
                thinking_id,  # Pass thinking_id positionally
            )

            if not response_set:
                logger.warning(f"{log_prefix}[Replier-{thinking_id}] LLM生成了一个空回复集。")
                return None

            # --- 准备并返回结果 --- #
            # logger.info(f"{log_prefix}[Replier-{thinking_id}] 成功生成了回复集: {' '.join(response_set)[:50]}...")
            return response_set

        except Exception as e:
            logger.error(f"{log_prefix}[Replier-{thinking_id}] Unexpected error in replier_work: {e}")
            logger.error(traceback.format_exc())
            return None

    # --- Methods moved from HeartFCController start ---
    async def _create_thinking_message(self, anchor_message: Optional[MessageRecv]) -> Optional[str]:
        """创建思考消息 (尝试锚定到 anchor_message)"""
        if not anchor_message or not anchor_message.chat_stream:
            logger.error(f"{self._get_log_prefix()} 无法创建思考消息，缺少有效的锚点消息或聊天流。")
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
        # Access MessageManager via controller
        self.heartfc_controller.MessageManager().add_message(thinking_message)
        return thinking_id

    async def _send_response_messages(
        self, anchor_message: Optional[MessageRecv], response_set: List[str], thinking_id: str
    ) -> Optional[MessageSending]:
        """发送回复消息 (尝试锚定到 anchor_message)"""
        if not anchor_message or not anchor_message.chat_stream:
            logger.error(f"{self._get_log_prefix()} 无法发送回复，缺少有效的锚点消息或聊天流。")
            return None

        chat = anchor_message.chat_stream
        container = self.heartfc_controller.MessageManager().get_container(chat.stream_id)
        thinking_message = None

        # 移除思考消息
        for msg in container.messages[:]:  # Iterate over a copy
            if isinstance(msg, MessageThinking) and msg.message_info.message_id == thinking_id:
                thinking_message = msg
                container.messages.remove(msg)  # Remove the message directly here
                logger.debug(f"{self._get_log_prefix()} Removed thinking message {thinking_id} via iteration.")
                break

        if not thinking_message:
            stream_name = chat_manager.get_stream_name(chat.stream_id) or chat.stream_id  # 获取流名称
            logger.warning(f"[{stream_name}] {thinking_id}，思考太久了，超时被移除")
            return None

        thinking_start_time = thinking_message.thinking_start_time
        message_set = MessageSet(chat, thinking_id)
        mark_head = False
        first_bot_msg = None
        bot_user_info = UserInfo(
            user_id=global_config.BOT_QQ,
            user_nickname=global_config.BOT_NICKNAME,
            platform=anchor_message.message_info.platform,
        )
        for msg_text in response_set:
            message_segment = Seg(type="text", data=msg_text)
            bot_message = MessageSending(
                message_id=thinking_id,  # 使用 thinking_id 作为批次标识
                chat_stream=chat,
                bot_user_info=bot_user_info,
                sender_info=anchor_message.message_info.user_info,  # 发送给锚点消息的用户
                message_segment=message_segment,
                reply=anchor_message,  # 回复锚点消息
                is_head=not mark_head,
                is_emoji=False,
                thinking_start_time=thinking_start_time,
            )
            if not mark_head:
                mark_head = True
                first_bot_msg = bot_message
            message_set.add_message(bot_message)

        self.heartfc_controller.MessageManager().add_message(message_set)
        return first_bot_msg

    async def _handle_emoji(self, anchor_message: Optional[MessageRecv], response_set: List[str], send_emoji: str = ""):
        """处理表情包 (尝试锚定到 anchor_message)"""

        if not anchor_message or not anchor_message.chat_stream:
            logger.error(f"{self._get_log_prefix()} 无法处理表情包，缺少有效的锚点消息或聊天流。")
            return

        chat = anchor_message.chat_stream
        # Access emoji_manager via controller
        emoji_manager_instance = self.heartfc_controller.emoji_manager
        if send_emoji:
            emoji_raw = await emoji_manager_instance.get_emoji_for_text(send_emoji)
        else:
            emoji_text_source = "".join(response_set) if response_set else ""
            emoji_raw = await emoji_manager_instance.get_emoji_for_text(emoji_text_source)

        if emoji_raw:
            emoji_path, _description = emoji_raw
            emoji_cq = image_path_to_base64(emoji_path)
            thinking_time_point = round(time.time(), 2)
            message_segment = Seg(type="emoji", data=emoji_cq)
            bot_user_info = UserInfo(
                user_id=global_config.BOT_QQ,
                user_nickname=global_config.BOT_NICKNAME,
                platform=anchor_message.message_info.platform,
            )
            bot_message = MessageSending(
                message_id="me" + str(thinking_time_point),  # 使用不同的 ID 前缀?
                chat_stream=chat,
                bot_user_info=bot_user_info,
                sender_info=anchor_message.message_info.user_info,
                message_segment=message_segment,
                reply=anchor_message,  # 回复锚点消息
                is_head=False,
                is_emoji=True,
            )
            # Access MessageManager via controller
            self.heartfc_controller.MessageManager().add_message(bot_message)
