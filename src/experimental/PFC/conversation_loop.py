import time
import asyncio
import datetime
import traceback
from typing import Dict, Any, List, TYPE_CHECKING
from dateutil import tz

from src.common.logger_manager import get_logger
from src.config.config import global_config
from .pfc_types import ConversationState  # 需要导入 ConversationState
from . import actions  # 需要导入 actions 模块

if TYPE_CHECKING:
    from .conversation import Conversation

logger = get_logger("pfc_loop")

# 时区配置
TIME_ZONE = tz.gettz("Asia/Shanghai")

MAX_CONSECUTIVE_LLM_ACTION_FAILURES = 3  # 可配置的最大LLM连续失败次数


async def run_conversation_loop(conversation_instance: "Conversation"):
    """
    核心的规划与行动循环 (PFC Loop)。
    """
    logger.debug(f"[私聊][{conversation_instance.private_name}] 进入 run_conversation_loop 循环。")

    if not conversation_instance._initialized:
        logger.error(f"[私聊][{conversation_instance.private_name}] 尝试在未初始化状态下运行规划循环，退出。")
        return

    _force_reflect_and_act_next_iter = False

    while conversation_instance.should_continue:
        loop_iter_start_time = time.time()
        current_force_reflect_and_act = _force_reflect_and_act_next_iter
        _force_reflect_and_act_next_iter = False

        logger.debug(
            f"[私聊][{conversation_instance.private_name}] 开始新一轮循环迭代 ({loop_iter_start_time:.2f}), force_reflect_next_iter: {current_force_reflect_and_act}, consecutive_llm_failures: {conversation_instance.consecutive_llm_action_failures}"
        )

        try:
            global TIME_ZONE
            if TIME_ZONE is None:
                configured_tz_loop = getattr(global_config, "TIME_ZONE", "Asia/Shanghai")
                TIME_ZONE = tz.gettz(configured_tz_loop)
                if TIME_ZONE is None:
                    logger.error(f"循环中: 配置的时区 '{configured_tz_loop}' 无效，将使用 'Asia/Shanghai'")
                    TIME_ZONE = tz.gettz("Asia/Shanghai")

            current_time_dt = datetime.datetime.now(TIME_ZONE)
            if conversation_instance.observation_info:
                time_str = current_time_dt.strftime("%Y-%m-%d %H:%M:%S %Z%z")
                conversation_instance.observation_info.current_time_str = time_str
            else:
                logger.warning(
                    f"[私聊][{conversation_instance.private_name}] ObservationInfo 未初始化，无法更新当前时间。"
                )
        except Exception as time_update_err:
            logger.error(
                f"[私聊][{conversation_instance.private_name}] 更新 ObservationInfo 当前时间时出错: {time_update_err}"
            )

        if (
            conversation_instance.ignore_until_timestamp
            and loop_iter_start_time < conversation_instance.ignore_until_timestamp
        ):
            if conversation_instance.idle_chat and conversation_instance.idle_chat._running:
                logger.debug(f"[私聊][{conversation_instance.private_name}] 对话被暂时忽略，暂停对该用户的主动聊天")
            sleep_duration = min(30, conversation_instance.ignore_until_timestamp - loop_iter_start_time)
            await asyncio.sleep(sleep_duration)
            continue
        elif (
            conversation_instance.ignore_until_timestamp
            and loop_iter_start_time >= conversation_instance.ignore_until_timestamp
        ):
            logger.info(
                f"[私聊][{conversation_instance.private_name}] 忽略时间已到 {conversation_instance.stream_id}，准备结束对话。"
            )
            conversation_instance.ignore_until_timestamp = None
            await conversation_instance.stop()
            continue
        else:
            pass

        try:
            if conversation_instance.conversation_info and conversation_instance._initialized:
                if (
                    conversation_instance.conversation_info.person_id
                    and conversation_instance.relationship_translator
                    and conversation_instance.person_info_mng
                ):
                    try:
                        numeric_relationship_value = await conversation_instance.person_info_mng.get_value(
                            conversation_instance.conversation_info.person_id, "relationship_value"
                        )
                        if not isinstance(numeric_relationship_value, (int, float)):
                            from bson.decimal128 import Decimal128

                            if isinstance(numeric_relationship_value, Decimal128):
                                numeric_relationship_value = float(numeric_relationship_value.to_decimal())
                            else:
                                numeric_relationship_value = 0.0
                        conversation_instance.conversation_info.relationship_text = (
                            await conversation_instance.relationship_translator.translate_relationship_value_to_text(
                                numeric_relationship_value
                            )
                        )
                    except Exception as e_rel:
                        logger.error(f"[私聊][{conversation_instance.private_name}] (Loop) 更新关系文本时出错: {e_rel}")
                        conversation_instance.conversation_info.relationship_text = "你们的关系是：普通。"
                if conversation_instance.mood_mng:
                    conversation_instance.conversation_info.current_emotion_text = (
                        conversation_instance.mood_mng.get_mood_prompt()
                    )

            if not all(
                [
                    conversation_instance.action_planner,
                    conversation_instance.observation_info,
                    conversation_instance.conversation_info,
                ]
            ):
                logger.error(
                    f"[私聊][{conversation_instance.private_name}] 核心组件未初始化，无法继续规划循环。将等待5秒后重试..."
                )
                await asyncio.sleep(5)
                continue

            planning_start_time = time.time()
            logger.debug(
                f"[私聊][{conversation_instance.private_name}] --- (Loop) 开始规划 ({planning_start_time:.2f}) ---"
            )
            if conversation_instance.conversation_info:
                conversation_instance.conversation_info.other_new_messages_during_planning_count = 0

            action, reason = await conversation_instance.action_planner.plan(
                conversation_instance.observation_info,
                conversation_instance.conversation_info,
                conversation_instance.conversation_info.last_successful_reply_action
                if conversation_instance.conversation_info
                else None,
                use_reflect_prompt=current_force_reflect_and_act,
            )

            logger.debug(
                f"[私聊][{conversation_instance.private_name}] (Loop) ActionPlanner.plan 完成，初步规划动作: {action}"
            )

            current_unprocessed_messages_after_plan = getattr(
                conversation_instance.observation_info, "unprocessed_messages", []
            )
            new_messages_during_action_planning: List[Dict[str, Any]] = []
            other_new_messages_during_action_planning: List[Dict[str, Any]] = []

            for msg_ap in current_unprocessed_messages_after_plan:
                msg_time_ap = msg_ap.get("time")
                sender_id_info_ap = msg_ap.get("user_info", {})
                sender_id_ap = str(sender_id_info_ap.get("user_id")) if sender_id_info_ap else None
                if msg_time_ap and msg_time_ap >= planning_start_time:
                    new_messages_during_action_planning.append(msg_ap)
                    if sender_id_ap != conversation_instance.bot_qq_str:
                        other_new_messages_during_action_planning.append(msg_ap)

            new_msg_count_action_planning = len(new_messages_during_action_planning)
            other_new_msg_count_action_planning = len(other_new_messages_during_action_planning)

            if conversation_instance.conversation_info and other_new_msg_count_action_planning > 0:
                pass

            should_interrupt_action_planning: bool = False
            interrupt_reason_action_planning: str = ""
            if action in ["wait", "listening"] and new_msg_count_action_planning > 0:
                should_interrupt_action_planning = True
                interrupt_reason_action_planning = f"规划 {action} 期间收到 {new_msg_count_action_planning} 条新消息"
            elif other_new_msg_count_action_planning > global_config.pfc_message_buffer_size:
                should_interrupt_action_planning = True
                interrupt_reason_action_planning = (
                    f"规划 {action} 期间收到 {other_new_msg_count_action_planning} 条来自他人的新消息"
                )

            if should_interrupt_action_planning:
                logger.info(
                    f"[私聊][{conversation_instance.private_name}] (Loop) 中断 '{action}' (在ActionPlanner.plan后)，原因: {interrupt_reason_action_planning}。重新规划..."
                )
                cancel_record_ap = {
                    "action": action,
                    "plan_reason": reason,
                    "status": "cancelled_due_to_new_messages_during_action_plan",
                    "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "final_reason": interrupt_reason_action_planning,
                }
                if conversation_instance.conversation_info:
                    if (
                        not hasattr(conversation_instance.conversation_info, "done_action")
                        or conversation_instance.conversation_info.done_action is None
                    ):
                        conversation_instance.conversation_info.done_action = []
                    conversation_instance.conversation_info.done_action.append(cancel_record_ap)
                    conversation_instance.conversation_info.last_successful_reply_action = None
                conversation_instance.state = ConversationState.ANALYZING
                await asyncio.sleep(0.1)
                continue

            # --- LLM Action Handling with Shield and Failure Count ---
            if action in ["direct_reply", "send_new_message"]:
                logger.debug(
                    f"[私聊][{conversation_instance.private_name}] (Loop) 动作 '{action}' 需要LLM生成，进入监控执行模式..."
                )
                llm_call_start_time = time.time()

                if conversation_instance.conversation_info:
                    conversation_instance.conversation_info.other_new_messages_during_planning_count = (
                        other_new_msg_count_action_planning
                    )

                llm_action_task = asyncio.create_task(
                    actions.handle_action(
                        conversation_instance,
                        action,
                        reason,
                        conversation_instance.observation_info,
                        conversation_instance.conversation_info,
                    )
                )

                interrupted_by_new_messages = False
                llm_action_completed_successfully = False
                action_outcome_processed = False  # Flag to ensure we process outcome only once

                while not llm_action_task.done() and not action_outcome_processed:
                    try:
                        # Shield the task so wait_for timeout doesn't cancel it directly
                        await asyncio.wait_for(asyncio.shield(llm_action_task), timeout=1.5)
                        # If wait_for completes without timeout, the shielded task is done (or errored/cancelled by itself)
                        action_outcome_processed = True  # Outcome will be processed outside this loop
                    except asyncio.TimeoutError:
                        # Shielded task didn't finish in 1.5s. This is our chance to check messages.
                        current_time_for_check = time.time()
                        logger.debug(
                            f"[私聊][{conversation_instance.private_name}] (Loop) LLM Monitor polling. llm_call_start_time: {llm_call_start_time:.2f}, current_check_time: {current_time_for_check:.2f}. Task still running, checking for new messages."
                        )

                        current_unprocessed_messages_during_llm = getattr(
                            conversation_instance.observation_info, "unprocessed_messages", []
                        )
                        other_new_messages_this_check: List[Dict[str, Any]] = []

                        logger.debug(
                            f"[私聊][{conversation_instance.private_name}] (Loop) Checking unprocessed_messages (count: {len(current_unprocessed_messages_during_llm)}):"
                        )
                        for msg_llm in current_unprocessed_messages_during_llm:
                            msg_time_llm = msg_llm.get("time")
                            sender_id_info_llm = msg_llm.get("user_info", {})
                            sender_id_llm = str(sender_id_info_llm.get("user_id")) if sender_id_info_llm else None
                            is_new_enough = msg_time_llm and msg_time_llm >= llm_call_start_time
                            is_other_sender = sender_id_llm != conversation_instance.bot_qq_str

                            time_str_for_log = f"{msg_time_llm:.2f}" if msg_time_llm is not None else "N/A"
                            logger.debug(
                                f"  - Msg ID: {msg_llm.get('message_id')}, Time: {time_str_for_log}, Sender: {sender_id_llm}. New enough? {is_new_enough}. Other sender? {is_other_sender}."
                            )

                            if is_new_enough and is_other_sender:
                                other_new_messages_this_check.append(msg_llm)

                        logger.debug(
                            f"[私聊][{conversation_instance.private_name}] (Loop) Found {len(other_new_messages_this_check)} 'other_new_messages_this_check'."
                        )

                        if len(other_new_messages_this_check) > global_config.pfc_message_buffer_size:
                            logger.info(
                                f"[私聊][{conversation_instance.private_name}] (Loop) LLM动作 '{action}' 执行期间收到 {len(other_new_messages_this_check)} 条来自他人的新消息，将取消LLM任务。"
                            )
                            if not llm_action_task.done():  # Check again before cancelling
                                llm_action_task.cancel()  # Now we explicitly cancel the original task
                            interrupted_by_new_messages = True
                            action_outcome_processed = True  # We've made a decision, exit monitoring
                        # else: continue polling if not enough new messages
                    # Shield ensures CancelledError from llm_action_task itself is caught by the outer try/except

                # After the monitoring loop (either task finished, or we decided to cancel it)
                # Await the task properly to get its result or handle its exception/cancellation
                action_final_status_in_history = "unknown"
                try:
                    await llm_action_task  # This will re-raise CancelledError if we cancelled it, or other exceptions

                    # If no exception, it means the task completed.
                    # actions.handle_action updates done_action, so we check its status.
                    if conversation_instance.conversation_info and conversation_instance.conversation_info.done_action:
                        # Check if done_action is not empty
                        if conversation_instance.conversation_info.done_action:
                            action_final_status_in_history = conversation_instance.conversation_info.done_action[
                                -1
                            ].get("status", "unknown")

                    if action_final_status_in_history in ["done", "done_no_reply"]:
                        logger.debug(
                            f"[私聊][{conversation_instance.private_name}] (Loop) LLM动作 '{action}' 任务最终成功完成 (status: {action_final_status_in_history})。"
                        )
                        conversation_instance.consecutive_llm_action_failures = 0
                        llm_action_completed_successfully = True
                    else:
                        logger.warning(
                            f"[私聊][{conversation_instance.private_name}] (Loop) LLM动作 '{action}' 任务完成但未成功 (status: {action_final_status_in_history})。"
                        )
                        if not interrupted_by_new_messages:
                            conversation_instance.consecutive_llm_action_failures += 1

                except asyncio.CancelledError:
                    logger.info(
                        f"[私聊][{conversation_instance.private_name}] (Loop) LLM动作 '{action}' 任务最终确认被取消。"
                    )
                    if not interrupted_by_new_messages:
                        conversation_instance.consecutive_llm_action_failures += 1
                        logger.warning(
                            f"[私聊][{conversation_instance.private_name}] (Loop) LLM任务因外部原因取消，连续失败次数: {conversation_instance.consecutive_llm_action_failures}"
                        )
                    else:  # interrupted_by_new_messages is True
                        logger.info(
                            f"[私聊][{conversation_instance.private_name}] (Loop) LLM任务因新消息被内部逻辑取消，不计为LLM失败。"
                        )

                except Exception as e_llm_final:
                    logger.error(
                        f"[私聊][{conversation_instance.private_name}] (Loop) LLM动作 '{action}' 任务执行时发生最终错误: {e_llm_final}"
                    )
                    logger.error(traceback.format_exc())
                    conversation_instance.state = ConversationState.ERROR
                    if not interrupted_by_new_messages:
                        conversation_instance.consecutive_llm_action_failures += 1

                # --- Post LLM Action Task Handling ---
                if not llm_action_completed_successfully:
                    last_action_record = {}
                    last_action_final_status = "unknown"
                    # 从 conversation_info.done_action 获取上一个动作的最终状态
                    if conversation_instance.conversation_info and conversation_instance.conversation_info.done_action:
                        if conversation_instance.conversation_info.done_action:  # 确保列表不为空
                            last_action_record = conversation_instance.conversation_info.done_action[-1]
                            last_action_final_status = last_action_record.get("status", "unknown")

                    if last_action_final_status == "max_checker_attempts_failed":
                        original_planned_action = last_action_record.get("action", "unknown_original_action")
                        original_plan_reason = last_action_record.get("plan_reason", "unknown_original_reason")
                        checker_fail_reason_from_history = last_action_record.get(
                            "final_reason", "ReplyChecker判定不合适"
                        )

                        logger.warning(
                            f"[私聊][{conversation_instance.private_name}] (Loop) 原规划动作 '{original_planned_action}' 因达到ReplyChecker最大尝试次数而失败。将强制执行 'wait' 动作。"
                        )

                        action_to_perform_now = "wait"  # 强制动作为 "wait"
                        reason_for_forced_wait = f"原动作 '{original_planned_action}' (规划原因: {original_plan_reason}) 因 ReplyChecker 多次判定不合适 ({checker_fail_reason_from_history}) 而失败，现强制等待。"

                        if conversation_instance.conversation_info:
                            # 确保下次规划不是基于这个失败的回复动作的追问
                            conversation_instance.conversation_info.last_successful_reply_action = None
                            # 重置连续LLM失败计数器，因为我们已经用特定的“等待”动作处理了这种失败类型
                            conversation_instance.consecutive_llm_action_failures = 0

                        logger.info(f"[私聊][{conversation_instance.private_name}] (Loop) 执行强制等待动作...")
                        await actions.handle_action(
                            conversation_instance,
                            action_to_perform_now,  # "wait"
                            reason_for_forced_wait,
                            conversation_instance.observation_info,
                            conversation_instance.conversation_info,
                        )
                        # "wait" 动作执行后，其内部逻辑会将状态设置为 ANALYZING (通过 finally 块)
                        # 所以循环的下一轮会自然地重新规划或根据等待结果行动
                        _force_reflect_and_act_next_iter = False  # 确保此路径不会强制反思
                        await asyncio.sleep(0.1)  # 短暂暂停，等待状态更新等
                        continue  # 进入主循环的下一次迭代

                    elif conversation_instance.consecutive_llm_action_failures >= MAX_CONSECUTIVE_LLM_ACTION_FAILURES:
                        logger.error(
                            f"[私聊][{conversation_instance.private_name}] (Loop) LLM相关动作连续失败或被取消 {conversation_instance.consecutive_llm_action_failures} 次。将强制等待并重置计数器。"
                        )

                        forced_wait_action_on_consecutive_failure = "wait"
                        reason_for_consecutive_failure_wait = (
                            f"LLM连续失败{conversation_instance.consecutive_llm_action_failures}次，强制等待"
                        )

                        conversation_instance.consecutive_llm_action_failures = 0

                        if conversation_instance.conversation_info:
                            conversation_instance.conversation_info.last_successful_reply_action = None

                        logger.info(f"[私聊][{conversation_instance.private_name}] (Loop) 执行强制等待动作...")
                        await actions.handle_action(
                            conversation_instance,
                            forced_wait_action_on_consecutive_failure,  # "wait"
                            reason_for_consecutive_failure_wait,
                            conversation_instance.observation_info,
                            conversation_instance.conversation_info,
                        )
                        _force_reflect_and_act_next_iter = False
                        conversation_instance.state = ConversationState.ANALYZING
                        await asyncio.sleep(1)
                        continue
                    else:
                        conversation_instance.state = ConversationState.ANALYZING
                        logger.info(
                            f"[私聊][{conversation_instance.private_name}] (Loop) LLM动作中断/失败，准备重新规划。Interrupted by new msgs: {interrupted_by_new_messages}, Consecutive LLM Failures: {conversation_instance.consecutive_llm_action_failures}"
                        )
                        await asyncio.sleep(0.1)
                        continue
            else:
                logger.debug(f"[私聊][{conversation_instance.private_name}] (Loop) 执行非LLM类动作 '{action}'...")
                conversation_instance.consecutive_llm_action_failures = 0
                logger.debug(
                    f"[私聊][{conversation_instance.private_name}] (Loop) 重置 consecutive_llm_action_failures due to non-LLM action."
                )

                if conversation_instance.conversation_info:
                    conversation_instance.conversation_info.other_new_messages_during_planning_count = (
                        other_new_msg_count_action_planning
                    )

                await actions.handle_action(
                    conversation_instance,
                    action,
                    reason,
                    conversation_instance.observation_info,
                    conversation_instance.conversation_info,
                )
                logger.debug(f"[私聊][{conversation_instance.private_name}] (Loop) 非LLM类动作 '{action}' 完成。")

            last_action_record = {}
            if conversation_instance.conversation_info and conversation_instance.conversation_info.done_action:
                if conversation_instance.conversation_info.done_action:
                    last_action_record = conversation_instance.conversation_info.done_action[-1]

            if (
                last_action_record.get("action") == "send_new_message"
                and last_action_record.get("status") == "done_no_reply"
            ):
                logger.info(
                    f"[私聊][{conversation_instance.private_name}] (Loop) 检测到 ReplyGenerator 决定不发送消息，下一轮将强制反思。"
                )
                _force_reflect_and_act_next_iter = True

            goal_ended: bool = False
            if (
                conversation_instance.conversation_info
                and hasattr(conversation_instance.conversation_info, "goal_list")
                and conversation_instance.conversation_info.goal_list
            ):
                last_goal_item = conversation_instance.conversation_info.goal_list[-1]
                current_goal = (
                    last_goal_item.get("goal")
                    if isinstance(last_goal_item, dict)
                    else (last_goal_item if isinstance(last_goal_item, str) else None)
                )
                if current_goal == "结束对话":
                    goal_ended = True

            last_action_record_for_end_check = {}
            if conversation_instance.conversation_info and conversation_instance.conversation_info.done_action:
                if conversation_instance.conversation_info.done_action:
                    last_action_record_for_end_check = conversation_instance.conversation_info.done_action[-1]

            action_ended: bool = (
                last_action_record_for_end_check.get("action") in ["end_conversation", "say_goodbye"]
                and last_action_record_for_end_check.get("status") == "done"
            )

            if goal_ended or action_ended:
                logger.info(f"[私聊][{conversation_instance.private_name}] (Loop) 检测到结束条件，停止循环。")
                await conversation_instance.stop()
                continue

        except asyncio.CancelledError:
            logger.info(f"[私聊][{conversation_instance.private_name}] (Loop) PFC 主循环任务被取消。")
            await conversation_instance.stop()
            break
        except Exception as loop_err:
            logger.error(f"[私聊][{conversation_instance.private_name}] (Loop) PFC 主循环出错: {loop_err}")
            logger.error(f"[私聊][{conversation_instance.private_name}] (Loop) {traceback.format_exc()}")
            conversation_instance.state = ConversationState.ERROR
            await asyncio.sleep(5)

        loop_duration = time.time() - loop_iter_start_time
        min_loop_interval = 0.1
        logger.debug(f"[私聊][{conversation_instance.private_name}] (Loop) 循环迭代耗时: {loop_duration:.3f} 秒。")
        if loop_duration < min_loop_interval:
            await asyncio.sleep(min_loop_interval - loop_duration)

    logger.info(
        f"[私聊][{conversation_instance.private_name}] (Loop) PFC 循环已退出 for stream_id: {conversation_instance.stream_id}"
    )
