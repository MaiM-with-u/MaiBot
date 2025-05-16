import time
import asyncio
import datetime
import traceback
from typing import Optional, TYPE_CHECKING

from src.common.logger_manager import get_logger
from .pfc_types import ConversationState  # 调整导入路径
from .observation_info import ObservationInfo  # 调整导入路径
from .conversation_info import ConversationInfo  # 调整导入路径

# 导入工厂类
from .action_factory import StandardActionFactory  # 调整导入路径

if TYPE_CHECKING:
    from .conversation import Conversation  # 调整导入路径

logger = get_logger("pfc_actions")  # 模块级别日志记录器


async def handle_action(
    conversation_instance: "Conversation",
    action: str,
    reason: str,
    observation_info: Optional[ObservationInfo],
    conversation_info: Optional[ConversationInfo],
):
    """
    处理由 ActionPlanner 规划出的具体行动。
    使用 ActionFactory 创建并执行相应的处理器。
    """
    # 检查对话实例是否已初始化
    if not conversation_instance._initialized:
        logger.error(f"[私聊][{conversation_instance.private_name}] 尝试在未初始化状态下处理动作 '{action}'。")
        return

    # 检查 observation_info 是否为空
    if not observation_info:
        logger.error(f"[私聊][{conversation_instance.private_name}] ObservationInfo 为空，无法处理动作 '{action}'。")
        # 如果 conversation_info 和 done_action 存在且不为空
        if conversation_info and hasattr(conversation_info, "done_action") and conversation_info.done_action:
            # 更新最后一个动作记录的状态和原因
            if conversation_info.done_action:  # 再次检查列表是否不为空
                conversation_info.done_action[-1].update({"status": "error", "final_reason": "ObservationInfo is None"})
        conversation_instance.state = ConversationState.ERROR  # 设置对话状态为错误
        return
    # 检查 conversation_info 是否为空
    if not conversation_info:
        logger.error(f"[私聊][{conversation_instance.private_name}] ConversationInfo 为空，无法处理动作 '{action}'。")
        conversation_instance.state = ConversationState.ERROR  # 设置对话状态为错误
        return

    logger.info(f"[私聊][{conversation_instance.private_name}] 开始处理动作: {action}, 原因: {reason}")
    action_start_time = time.time()  # 记录动作开始时间

    # 当前动作记录
    current_action_record = {
        "action": action,  # 动作类型
        "plan_reason": reason,  # 规划原因
        "status": "start",  # 初始状态为 "start"
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # 当前时间
        "final_reason": None,  # 最终原因，默认为 None
    }
    # 如果 done_action 不存在或为空，则初始化
    if not hasattr(conversation_info, "done_action") or conversation_info.done_action is None:
        conversation_info.done_action = []
    conversation_info.done_action.append(current_action_record)  # 添加当前动作记录
    action_index = len(conversation_info.done_action) - 1  # 获取当前动作记录的索引

    action_successful: bool = False  # 动作是否成功，默认为 False
    final_status: str = "recall"  # 最终状态，默认为 "recall"
    final_reason: str = "动作未成功执行"  # 最终原因，默认为 "动作未成功执行"

    factory = StandardActionFactory()  # 创建标准动作工厂实例
    action_handler = factory.create_action_handler(action, conversation_instance)  # 创建动作处理器

    try:
        # 执行动作处理器
        action_successful, final_status, final_reason = await action_handler.execute(
            reason, observation_info, conversation_info, action_start_time, current_action_record
        )

        # 动作执行后的逻辑 (例如更新 last_successful_reply_action 等)
        # 此部分之前位于每个 if/elif 块内部
        # 如果动作不是回复类型的动作
        if action not in ["direct_reply", "send_new_message", "say_goodbye", "send_memes"]:
            conversation_info.last_successful_reply_action = None  # 清除上次成功回复动作
            conversation_info.last_reply_rejection_reason = None  # 清除上次回复拒绝原因
            conversation_info.last_rejected_reply_content = None  # 清除上次拒绝的回复内容

        # 如果动作不是发送表情包或发送表情包失败，则清除表情查询
        if action != "send_memes" or not action_successful:
            if hasattr(conversation_info, "current_emoji_query"):
                conversation_info.current_emoji_query = None

    except asyncio.CancelledError:  # 捕获任务取消错误
        logger.warning(f"[私聊][{conversation_instance.private_name}] 处理动作 '{action}' 时被取消。")
        final_status = "cancelled"  # 设置最终状态为 "cancelled"
        final_reason = "动作处理被取消"
        # 如果 conversation_info 存在
        if conversation_info:
            conversation_info.last_successful_reply_action = None  # 清除上次成功回复动作
        raise  # 重新抛出异常，由循环处理
    except Exception as handle_err:  # 捕获其他异常
        logger.error(f"[私聊][{conversation_instance.private_name}] 处理动作 '{action}' 时出错: {handle_err}")
        logger.error(f"[私聊][{conversation_instance.private_name}] {traceback.format_exc()}")
        final_status = "error"  # 设置最终状态为 "error"
        final_reason = f"处理动作时出错: {handle_err}"
        conversation_instance.state = ConversationState.ERROR  # 设置对话状态为错误
        # 如果 conversation_info 存在
        if conversation_info:
            conversation_info.last_successful_reply_action = None  # 清除上次成功回复动作
        action_successful = False  # 确保动作为不成功

    finally:
        # 更新动作历史记录
        # 检查 done_action 属性是否存在且不为空，并且索引有效
        if (
            hasattr(conversation_info, "done_action")
            and conversation_info.done_action
            and action_index < len(conversation_info.done_action)
        ):
            # 如果动作成功且最终状态不是 "done" 或 "done_no_reply"，则设置为 "done"
            if action_successful and final_status not in ["done", "done_no_reply"]:
                final_status = "done"
            # 如果动作成功且最终原因未设置或为默认值
            if action_successful and (not final_reason or final_reason == "动作未成功执行"):
                final_reason = f"动作 {action} 成功完成"
                # 如果是发送表情包且 current_emoji_query 存在（理想情况下从处理器获取描述）
                if action == "send_memes" and conversation_info.current_emoji_query:
                    pass  # 占位符 - 表情描述最好从处理器的执行结果中获取并用于原因

            # 更新动作记录
            conversation_info.done_action[action_index].update(
                {
                    "status": final_status,  # 最终状态
                    "time_completed": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # 完成时间
                    "final_reason": final_reason,  # 最终原因
                    "duration_ms": int((time.time() - action_start_time) * 1000),  # 持续时间（毫秒）
                }
            )
        else:  # 如果无法更新动作历史记录
            logger.error(
                f"[私聊][{conversation_instance.private_name}] 无法更新动作历史记录，done_action 无效或索引 {action_index} 超出范围。"
            )

        # 根据最终状态设置对话状态
        if final_status in ["done", "done_no_reply", "recall"]:
            conversation_instance.state = ConversationState.ANALYZING  # 设置为分析中
        elif final_status in ["error", "max_checker_attempts_failed"]:
            conversation_instance.state = ConversationState.ERROR  # 设置为错误
        # 其他状态如 LISTENING, WAITING, IGNORED, ENDED 在各自的处理器内部或由循环设置。

        # 此处移至 try 块以确保即使在发生异常之前也运行
        # 如果动作不是回复类型的动作
        if action not in ["direct_reply", "send_new_message", "say_goodbye", "send_memes"]:
            if conversation_info:  # 再次检查 conversation_info 是否不为 None
                conversation_info.last_successful_reply_action = None  # 清除上次成功回复动作
                conversation_info.last_reply_rejection_reason = None  # 清除上次回复拒绝原因
                conversation_info.last_rejected_reply_content = None  # 清除上次拒绝的回复内容
        # 如果动作不是发送表情包或发送表情包失败
        if action != "send_memes" or not action_successful:
            # 如果 conversation_info 存在且有 current_emoji_query 属性
            if conversation_info and hasattr(conversation_info, "current_emoji_query"):
                conversation_info.current_emoji_query = None  # 清除当前表情查询

        log_final_reason_msg = final_reason if final_reason else "无明确原因"  # 记录的最终原因消息
        # 如果最终状态为 "done"，动作成功，且是直接回复或发送新消息，并且有生成的回复
        if (
            final_status == "done"
            and action_successful
            and action in ["direct_reply", "send_new_message"]
            and hasattr(conversation_instance, "generated_reply")
            and conversation_instance.generated_reply
        ):
            log_final_reason_msg += f" (发送内容: '{conversation_instance.generated_reply[:30]}...')"
        # elif final_status == "done" and action_successful and action == "send_memes":
        # 表情包的日志记录在其处理器内部或通过下面的通用日志处理

        logger.info(
            f"[私聊][{conversation_instance.private_name}] 动作 '{action}' 处理完成。最终状态: {final_status}, 原因: {log_final_reason_msg}"
        )
