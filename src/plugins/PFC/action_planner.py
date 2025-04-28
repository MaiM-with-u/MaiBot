# PFC/action_planner.py
import traceback
import time
from typing import Tuple, Optional, Dict, Any # 增加了 Optional, Dict, Any
from src.common.logger_manager import get_logger
# from ..models.utils_model import LLMRequest # Assuming LLMRequest is needed, ensure import path is correct
from src.common.utils_llm import LLMRequest # Using updated common location assumption
from ...config.config import global_config
from .chat_observer import ChatObserver
from .pfc_utils import get_items_from_json
from src.individuality.individuality import Individuality
from .observation_info import ObservationInfo
from .conversation_info import ConversationInfo
from src.plugins.utils.chat_message_builder import build_readable_messages


logger = get_logger("pfc_action_planner")


# --- 定义 Prompt 模板 ---

# Prompt(1): 首次回复或非连续回复时的决策 Prompt
PROMPT_INITIAL_REPLY = """{persona_text}。现在你在参与一场QQ私聊，请根据以下【所有信息】审慎且灵活的决策下一步行动，可以回复，可以倾听，可以调取知识，甚至可以屏蔽对方：

【当前对话目标】
{goals_str}
【你现在的想法】
{pfc_heartflow}

【最近行动历史概要】
{action_history_summary}
【上一次行动的详细情况和结果】
{last_action_context}
【时间和超时提示】
{time_since_last_bot_message_info}{timeout_context}
【最近的对话记录】(包括你已成功发送的消息 和 新收到的消息)
{chat_history_text}

------
可选行动类型以及解释：
fetch_knowledge: 需要调取知识，当需要专业知识或特定信息时选择，对方若提到你不太认识的人名或实体也可以尝试选择
listening: 倾听对方发言，当你认为对方话才说到一半，发言明显未结束时选择
direct_reply: 直接回复对方
rethink_goal: 思考一个对话目标，当你觉得目前对话需要目标，或当前目标不再适用，或话题卡住时选择。注意私聊的环境是灵活的，有可能需要经常选择
end_conversation: 结束对话，对方长时间没回复或者当你觉得对话告一段落时可以选择
block_and_ignore: 更加极端的结束对话方式，直接结束对话并在一段时间内无视对方所有发言（屏蔽），当对话让你感到十分不适，或你遭到各类骚扰时选择

请以JSON格式输出你的决策：
{{
    "action": "选择的行动类型 (必须是上面列表中的一个)",
    "reason": "选择该行动的详细原因 (必须有解释你是如何根据“上一次行动结果”、“对话记录”、你的想法和自身设定人设做出合理判断的)"
}}

注意：请严格按照JSON格式输出，不要包含任何其他内容。""" # Updated reason instruction

# Prompt(2): 上一次成功回复后，决定继续发言时的决策 Prompt
PROMPT_FOLLOW_UP = """{persona_text}。现在你在参与一场QQ私聊，刚刚你已经回复了对方，请根据以下【所有信息】审慎且灵活的决策下一步行动，可以继续发送新消息，可以等待，可以倾听，可以调取知识，甚至可以屏蔽对方：

【当前对话目标】
{goals_str}
【你现在的想法】
{pfc_heartflow}

【最近行动历史概要】
{action_history_summary}
【上一次行动的详细情况和结果】
{last_action_context}
【时间和超时提示】
{time_since_last_bot_message_info}{timeout_context}
【最近的对话记录】(包括你已成功发送的消息 和 新收到的消息)
{chat_history_text}

------
可选行动类型以及解释：
fetch_knowledge: 需要调取知识，当需要专业知识或特定信息时选择，对方若提到你不太认识的人名或实体也可以尝试选择
wait: 暂时不说话，留给对方交互空间，等待对方回复（尤其是在你刚发言后、或上次发言因重复、发言过多被拒时、或不确定做什么时，这是不错的选择）
listening: 倾听对方发言（虽然你刚发过言，但如果对方立刻回复且明显话没说完，可以选择这个）
send_new_message: 发送一条新消息继续对话，允许适当的追问、补充、深入话题，或开启相关新话题。**但是避免在因重复被拒后立即使用，也不要在对方没有回复的情况下过多的“消息轰炸”或重复发言**
rethink_goal: 思考一个对话目标，当你觉得目前对话需要目标，或当前目标不再适用，或话题卡住时选择。注意私聊的环境是灵活的，有可能需要经常选择
end_conversation: 结束对话，对方长时间没回复或者当你觉得对话告一段落时可以选择
block_and_ignore: 更加极端的结束对话方式，直接结束对话并在一段时间内无视对方所有发言（屏蔽），当对话让你感到十分不适，或你遭到各类骚扰时选择

请以JSON格式输出你的决策：
{{
    "action": "选择的行动类型 (必须是上面列表中的一个)",
    "reason": "选择该行动的详细原因 (必须有解释你是如何根据“上一次行动结果”、“对话记录”、你的想法和自身设定人设做出合理判断的。请说明你为什么选择继续发言而不是等待，以及打算发送什么类型的新消息连续发言，必须记录已经发言了几次)"
}}

注意：请严格按照JSON格式输出，不要包含任何其他内容。""" # Updated reason instruction


# ActionPlanner 类定义，顶格
class ActionPlanner:
    """行动规划器"""

    def __init__(self, stream_id: str):
        # Ensure correct LLM config path/structure
        try:
            self.llm = LLMRequest(
                model=global_config.llm_PFC_action_planner,
                temperature=global_config.llm_PFC_action_planner.get("temp", 0.7), # Use .get for safety
                max_tokens=global_config.llm_PFC_action_planner.get("max_tokens", 1500), # Use .get for safety
                request_type="action_planning",
            )
        except AttributeError:
             logger.error("Config error: llm_PFC_action_planner not found or missing keys ('temp'/'max_tokens'). Using fallback.")
             # Fallback or raise error
             self.llm = LLMRequest(model=global_config.llm_normal, temperature=0.7, max_tokens=1000, request_type="action_planning_fallback")

        # Load personality/identity prompts
        self.individuality = Individuality.get_instance() # Store instance
        self.personality_info = self.individuality.get_prompt(type="personality", x_person=2, level=3)
        self.identity_detail_info = self.individuality.get_prompt(type="identity", x_person=2, level=2)

        self.name = global_config.BOT_NICKNAME
        self.chat_observer = ChatObserver.get_instance(stream_id)

    # 修改 plan 方法签名，增加 pfc_heartflow 参数
    async def plan(
        self,
        observation_info: ObservationInfo,
        conversation_info: ConversationInfo,
        last_successful_reply_action: Optional[str],
        pfc_heartflow: Optional[str], # <--- 新增参数
    ) -> Tuple[str, str]:
        """规划下一步行动

        Args:
            observation_info: 决策信息
            conversation_info: 对话信息
            last_successful_reply_action: 上一次成功的回复动作类型
            pfc_heartflow: 当前的心流文本 # <--- 新增参数说明

        Returns:
            Tuple[str, str]: (行动类型, 行动原因)
        """
        # --- 获取 Bot 上次发言时间信息 ---
        time_since_last_bot_message_info = ""
        bot_last_speak_time = observation_info.last_bot_speak_time # Use ObservationInfo directly
        if bot_last_speak_time:
            time_diff = time.time() - bot_last_speak_time
            if time_diff < 3600: # Show within an hour
                 time_since_last_bot_message_info = f"提示：你上一条成功发送的消息是在 {time_diff:.1f} 秒前。\n"

        # --- 获取超时提示信息 ---
        timeout_context = ""
<<<<<<< HEAD
        # Check for timeout goal added by Waiter
        if hasattr(conversation_info, "goal_list") and conversation_info.goal_list:
             last_goal_item = conversation_info.goal_list[-1]
             goal_text = ""
             reason_text = ""
             if isinstance(last_goal_item, dict):
                 goal_text = last_goal_item.get("goal", "")
                 reason_text = last_goal_item.get("reason", "") # Get reason from dict
             elif isinstance(last_goal_item, tuple) and len(last_goal_item) > 0:
                 goal_text = last_goal_item[0]
                 if len(last_goal_item) > 1:
                     reason_text = last_goal_item[1] # Get reason from tuple

             # Check if goal indicates a wait timeout
             if "分钟，思考接下来要做什么" in goal_text or "对方话说一半消失了" in goal_text:
                 timeout_context = f"重要提示：检测到等待超时。({reason_text}) 请基于此情况规划下一步。\n"

=======
        try:
            if hasattr(conversation_info, "goal_list") and conversation_info.goal_list:
                last_goal_dict = conversation_info.goal_list[-1]
                if isinstance(last_goal_dict, dict) and "goal" in last_goal_dict:
                    last_goal_text = last_goal_dict["goal"]
                    if isinstance(last_goal_text, str) and "分钟，思考接下来要做什么" in last_goal_text:
                        try:
                            timeout_minutes_text = last_goal_text.split("，")[0].replace("你等待了", "")
                            timeout_context = f"重要提示：对方已经长时间（{timeout_minutes_text}）没有回复你的消息了（这可能代表对方繁忙/不想回复/没注意到你的消息等情况，或在对方看来本次聊天已告一段落），请基于此情况规划下一步。\n"
                        except Exception:
                            timeout_context = "重要提示：对方已经长时间没有回复你的消息了（这可能代表对方繁忙/不想回复/没注意到你的消息等情况，或在对方看来本次聊天已告一段落），请基于此情况规划下一步。\n"
            else:
                logger.debug("Conversation info goal_list is empty or not available for timeout check.")
        except AttributeError:
            logger.warning("ConversationInfo object might not have goal_list attribute yet for timeout check.")
        except Exception as e:
            logger.warning(f"检查超时目标时出错: {e}")
>>>>>>> 3cfa1e6b17340f82f2937a2243b8e99030196294

        # --- 构建通用 Prompt 参数 ---
        logger.debug(f"开始规划行动：当前目标: {getattr(conversation_info, 'goal_list', '不可用')}")

        # 构建对话目标 (goals_str)
<<<<<<< HEAD
        goals_str = "- 目前没有明确对话目标，请考虑设定一个。\n" # Default
        if hasattr(conversation_info, "goal_list") and conversation_info.goal_list:
            temp_goals_str = ""
            for goal_reason in conversation_info.goal_list:
                goal = "目标内容缺失"
                reasoning = "没有明确原因"
                if isinstance(goal_reason, tuple) and len(goal_reason) > 0:
                    goal = goal_reason[0]
                    if len(goal_reason) > 1: reasoning = goal_reason[1]
                elif isinstance(goal_reason, dict):
                    goal = goal_reason.get("goal", "目标内容缺失")
                    reasoning = goal_reason.get("reason", "没有明确原因") # Use 'reason' key based on Waiter
                else:
                    goal = str(goal_reason)

                goal = str(goal) if goal is not None else "目标内容缺失"
                reasoning = str(reasoning) if reasoning is not None else "没有明确原因"
                temp_goals_str += f"- 目标：{goal}\n  原因：{reasoning}\n"
            if temp_goals_str: # Only overwrite default if goals were found
                goals_str = temp_goals_str
=======
        goals_str = ""
        try:
            if hasattr(conversation_info, "goal_list") and conversation_info.goal_list:
                for goal_reason in conversation_info.goal_list:
                    if isinstance(goal_reason, dict):
                        goal = goal_reason.get("goal", "目标内容缺失")
                        reasoning = goal_reason.get("reasoning", "没有明确原因")
                    else:
                        goal = str(goal_reason)
                        reasoning = "没有明确原因"

                    goal = str(goal) if goal is not None else "目标内容缺失"
                    reasoning = str(reasoning) if reasoning is not None else "没有明确原因"
                    goals_str += f"- 目标：{goal}\n  原因：{reasoning}\n"

                if not goals_str:
                    goals_str = "- 目前没有明确对话目标，请考虑设定一个。\n"
            else:
                goals_str = "- 目前没有明确对话目标，请考虑设定一个。\n"
        except AttributeError:
            logger.warning("ConversationInfo object might not have goal_list attribute yet.")
            goals_str = "- 获取对话目标时出错。\n"
        except Exception as e:
            logger.error(f"构建对话目标字符串时出错: {e}")
            goals_str = "- 构建对话目标时出错。\n"
>>>>>>> 3cfa1e6b17340f82f2937a2243b8e99030196294

        # 获取聊天历史记录 (chat_history_text)
        chat_history_text = "还没有聊天记录。\n" # Default
        if hasattr(observation_info, 'chat_history_str') and observation_info.chat_history_str:
            chat_history_text = observation_info.chat_history_str + "\n" # Ensure newline

        # Append unprocessed messages if any
        if hasattr(observation_info, 'new_messages_count') and observation_info.new_messages_count > 0:
             if hasattr(observation_info, 'unprocessed_messages') and observation_info.unprocessed_messages:
                 new_messages_list = observation_info.unprocessed_messages
                 try:
                    # Ensure build_readable_messages exists and handles the list format correctly
                    new_messages_str = await build_readable_messages(
                        new_messages_list,
                        replace_bot_name=True,
                        merge_messages=False,
                        timestamp_mode="relative",
                        read_mark=0.0, # Assuming this param exists
                    )
                    chat_history_text += f"\n--- 以下是 {observation_info.new_messages_count} 条新收到的消息 ---\n{new_messages_str}\n"
                 except Exception as build_err:
                    logger.error(f"Error building readable messages: {build_err}")
                    chat_history_text += "\n--- (无法格式化新消息) ---\n"
             else:
                 logger.warning("new_messages_count > 0 but unprocessed_messages is empty/missing.")
                 chat_history_text += f"\n--- (有 {observation_info.new_messages_count} 条新消息，但无法显示内容) ---\n"


        # 构建 Persona 文本 (persona_text)
        # Using stored Individuality instance
        identity_details_only = self.identity_detail_info
        identity_addon = ""
        if isinstance(identity_details_only, str):
            pronouns = ["你", "我", "他"]
            for p in pronouns:
                if identity_details_only.startswith(p):
                    identity_details_only = identity_details_only[len(p) :]
                    break
            if identity_details_only.endswith("。"):
                identity_details_only = identity_details_only[:-1]
            cleaned_details = identity_details_only.strip(",， ")
            if cleaned_details:
                identity_addon = f"并且{cleaned_details}"
        persona_text = f"你的名字是{self.name}，{self.personality_info}{identity_addon}。"

        # 构建行动历史和上一次行动结果 (action_history_summary, last_action_context)
        action_history_summary = "你最近执行的行动历史：\n"
        last_action_context = "关于你【上一次尝试】的行动：\n"
        action_history_list = []
        if hasattr(conversation_info, "done_action") and conversation_info.done_action:
            action_history_list = conversation_info.done_action[-5:] # Get last 5

        if not action_history_list:
            action_history_summary += "- 还没有执行过行动。\n"
            last_action_context += "- 这是你规划的第一个行动。\n"
        else:
            for i, action_data in enumerate(action_history_list):
                # Default values
                action_type = "未知行动"
                plan_reason = "未知原因"
                status = "未知状态"
                final_reason = ""
                action_time = "未知时间"

                # Check if action_data is a dictionary (new format)
                if isinstance(action_data, dict):
                    action_type = action_data.get("action", action_type)
                    plan_reason = action_data.get("plan_reason", plan_reason)
                    status = action_data.get("status", status)
                    final_reason = action_data.get("final_reason", "") # Get final_reason if exists
                    action_time = action_data.get("time", action_time)
                else:
                     # Handle potential old format or unexpected data gracefully
                     logger.warning(f"Unexpected action history format: {action_data}")
                     action_type = str(action_data) # Basic representation

                # Build summary line
                reason_text = f", 失败/取消原因: {final_reason}" if final_reason else ""
                summary_line = f"- 时间:{action_time}, 尝试行动:'{action_type}', 状态:{status}{reason_text}"
                action_history_summary += summary_line + "\n"

                # Build context for the very last action
                if i == len(action_history_list) - 1:
                    last_action_context += f"- 上次【规划】的行动是: '{action_type}'\n"
                    last_action_context += f"- 当时规划的【原因】是: {plan_reason}\n"
                    if status == "done":
                        last_action_context += "- 该行动已【成功执行】。\n"
                    elif status == "recall":
                        last_action_context += "- 但该行动最终【未能执行/被取消】。\n"
                        if final_reason:
                            last_action_context += f"- 【重要】失败/取消的具体原因是: “{final_reason}”\n"
                        else:
                            last_action_context += "- 【重要】失败/取消原因未明确记录。\n"
                    else:
                        # Handle other potential statuses like 'start' or unexpected ones
                        last_action_context += f"- 该行动当前状态: {status} (未完成或状态未知)\n"


        # --- 选择 Prompt ---
        if last_successful_reply_action in ["direct_reply", "send_new_message"]:
            prompt_template = PROMPT_FOLLOW_UP
            logger.debug("使用 PROMPT_FOLLOW_UP (追问决策)")
        else:
            prompt_template = PROMPT_INITIAL_REPLY
            logger.debug("使用 PROMPT_INITIAL_REPLY (首次/非连续回复决策)")

        # --- 格式化最终的 Prompt ---
        # Provide default for heartflow if None or empty
        heartflow_for_prompt = pfc_heartflow if pfc_heartflow else "你现在还没有明确的想法，请先思考。"

        try:
            prompt = prompt_template.format(
                persona_text=persona_text,
                goals_str=goals_str.strip(), # Remove leading/trailing whitespace
                pfc_heartflow=heartflow_for_prompt, # <--- 传入心流
                action_history_summary=action_history_summary.strip(),
                last_action_context=last_action_context.strip(),
                time_since_last_bot_message_info=time_since_last_bot_message_info,
                timeout_context=timeout_context,
                chat_history_text=chat_history_text.strip(),
            )
        except KeyError as e:
             logger.error(f"格式化行动规划 Prompt 时出错，缺少键: {e}")
             # Handle error: maybe return a default action or raise
             return "wait", f"内部错误：无法格式化行动规划提示（缺少 {e}）"

        logger.debug(f"发送到LLM的最终提示词:\n------\n{prompt}\n------")
        try:
            content, _ = await self.llm.generate_response_async(prompt)
            logger.debug(f"LLM原始返回内容: {content}")

            # Use default_values in get_items_from_json for robustness
            success, result = get_items_from_json(
                content,
                "action",
                "reason",
                default_values={"action": "wait", "reason": "LLM返回格式错误或未提供原因，默认等待"},
                required_types={"action": str, "reason": str} # Add type checking
            )

            action = result.get("action", "wait") # Ensure default if key somehow missing after get_items
            reason = result.get("reason", "LLM未提供原因，默认等待")

            # 验证action类型
            valid_actions = [
                "direct_reply",
                "send_new_message",
                "fetch_knowledge",
                "wait",
                "listening",
                "rethink_goal",
                "end_conversation",
                "block_and_ignore",
            ]
            if action not in valid_actions:
                logger.warning(f"LLM返回了未知的行动类型: '{action}'，强制改为 wait")
                reason = f"(原始行动'{action}'无效，已强制改为wait) {reason}"
                action = "wait"

            # Sanitize reason (optional, e.g., remove extra quotes)
            reason = reason.strip().strip('"')

            logger.info(f"规划的行动: {action}")
            logger.info(f"行动原因: {reason}")
            return action, reason

        except Exception as e:
            logger.error(f"规划行动时调用 LLM 或处理结果出错: {str(e)}")
            logger.error(traceback.format_exc()) # Log full traceback
            return "wait", f"行动规划处理中发生错误，暂时等待: {str(e)}"